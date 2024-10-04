import json
import os
import streamlit as st
import mysql.connector
from mysql.connector import Error
import subprocess
from datetime import datetime, timedelta
import cv2
import face_recognition
import numpy as np
import time
import paho.mqtt.client as mqtt
import pickle
import signal
import psutil

LOCK_FILE = "/tmp/saida_streamlit.lock"  # Arquivo de bloqueio

def verificar_processos(nome_processo, script):
    """Verifica se um processo com o nome especificado e script está rodando."""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Verifica se o processo é 'streamlit' e se está rodando o script correto (saida.py)
            if nome_processo in proc.info['name'] and script in " ".join(proc.info['cmdline']):
                return proc.info['pid']
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return None

def iniciar_streamlit():
    pid = verificar_processos("streamlit", "saida.py")
    if pid:
        print(f"O Streamlit já está rodando para saida.py com o PID {pid}.")
    else:
        print("Iniciando o Streamlit para saida.py...")
        subprocess.Popen(['streamlit', 'run', '/home/facial/saida.py'])
        print("Streamlit iniciado.")
        # Cria um arquivo de bloqueio (lock file) para evitar múltiplas execuções
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))

def verificar_lock_file():
    """Verifica se o arquivo de bloqueio existe e se o processo correspondente ainda está rodando."""
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE, "r") as f:
            pid = int(f.read().strip())
        # Verifica se o processo ainda está ativo
        if psutil.pid_exists(pid):
            print(f"O processo Streamlit com PID {pid} já está rodando.")
            return True
        else:
            print(f"O processo com PID {pid} não está mais ativo. Removendo o arquivo de bloqueio.")
            os.remove(LOCK_FILE)  # Remove o arquivo se o processo não estiver ativo
            return False
    return False

def reiniciar_streamlit():
    pid = verificar_processos("streamlit", "saida.py")
    
    if pid:
        print(f"Encerrando o processo Streamlit (PID: {pid})...")
        os.kill(pid, signal.SIGTERM)
        
        # Espera o encerramento do processo anterior
        os.waitpid(pid, 0)

        time.sleep(10)
        
        print("Iniciando o Streamlit novamente...")
        
        # Remover o arquivo de bloqueio antigo antes de reiniciar
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
        
        subprocess.Popen(['streamlit', 'run', '/home/facial/saida.py'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Streamlit reiniciado.")
        
        # Atualizar o arquivo de bloqueio com o novo PID
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    else:
        print("Nenhum processo Streamlit para saida.py encontrado, iniciando um novo...")

        time.sleep(5)

        iniciar_streamlit()

# Verifica se o Streamlit já está em execução diretamente no sistema e através do lock file
if not verificar_lock_file():
    iniciar_streamlit()


# Configurações do MQTT e banco---------------------------------------------
broker = "datawatcher360.online"
topico = "vasco1/facial"
stream = "http://root:samtech123@172.16.20.156/video1s3.mjpg"  # VIVOTEK
hostmysql = "localhost"
#hostmysql = "172.16.20.137"
intervalo_frame = 0.25

# Função para gerar cliente_id
def gerar_cliente_id(connection):
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT MAX(CAST(cliente_id AS UNSIGNED)) FROM clientes")
        result = cursor.fetchone()
        max_id = int(result[0]) if result[0] is not None else 0
        return max_id + 1
    except Error as e:
        st.error(f"Erro ao gerar cliente_id: {e}")
        return None

# Função para ajustar o timestamp com o fuso horário de GMT-3
def ajustar_para_gmt3(timestamp):
    return timestamp - timedelta(hours=3)

# Função para conectar ao MariaDB
def conectar_mariadb():
    try:
        connection = mysql.connector.connect(
            host=hostmysql,
            database='mendes',
            user='seu_usuario_mysql',
            password='sua_senha_mysql'
        )
        if connection.is_connected():
            return connection
    except Error as e:
        st.error(f"Erro ao conectar ao MariaDB: {e}")
        return None

# Função para carregar encodings e IDs do banco de dados
def carregar_encodings_do_banco(connection):
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT cliente_id, encoding FROM encodings")
        result = cursor.fetchall()

        clienteIds = []
        encodeListKnown = []

        for row in result:
            cliente_id = row[0]
            encoding = pickle.loads(row[1])
            clienteIds.append(cliente_id)
            encodeListKnown.append(encoding)

        return encodeListKnown, clienteIds
    except Error as e:
        st.error(f"Erro ao carregar encodings do banco: {e}")
        return [], []

# Função para buscar informações do cliente
def buscar_informacoes_cliente(connection, cliente_id):
    try:
        cursor = connection.cursor(dictionary=True)
        query = "SELECT * FROM clientes WHERE cliente_id = %s"
        cursor.execute(query, (cliente_id,))
        
        # Verifica se há resultados
        result = cursor.fetchone()
        if result is None:
            st.warning(f"Nenhum cliente encontrado com o cliente_id {cliente_id}")
            return None

        return result
    except Error as e:
        st.error(f"Erro ao buscar informações do cliente: {e}")
        return None


# Função para registrar log de acesso no banco de dados
def registrar_log_acesso(connection, cliente_id, tipo_acesso):
    try:
        cursor = connection.cursor()
        query = """
            INSERT INTO log_acessos (cliente_id, tipo_acesso, data_hora)
            VALUES (%s, %s, NOW())
        """
        cursor.execute(query, (cliente_id, tipo_acesso))
        connection.commit()
    except Error as e:
        st.error(f"Erro ao registrar log de acesso: {e}")
    finally:
        if cursor:
            cursor.close()


def pagina_adicionar_cliente():
    st.header("Adicionar Novo Cliente")
    nome = st.text_input("Nome")
    profissao = st.text_input("Profissão")
    cpf = st.text_input("CPF", max_chars=14, help="Insira um CPF válido")
    telefone = st.text_input("Telefone", max_chars=15)
    vendedor = st.text_input("Vendedor", max_chars=255)  # Novo campo vendedor
    ano_cliente = st.date_input("Data de Início como Cliente", value=datetime.now().date())
    imagem_cliente = st.file_uploader("Upload de Imagem", type=["jpg", "jpeg"])

    if st.button("Adicionar Cliente"):
        if nome and cpf and telefone and vendedor and imagem_cliente:
            connection = conectar_mariadb()
            if connection:
                try:
                    cliente_id = gerar_cliente_id(connection)
                    img_path = f'/home/files/facialfotos/{cliente_id}.jpg'
                    with open(img_path, "wb") as f:
                        f.write(imagem_cliente.getbuffer())
                    
                    adicionar_cliente_banco(connection, cliente_id, nome, profissao, cpf, telefone, int(time.mktime(ano_cliente.timetuple())), vendedor)

                    st.success(f"Cliente {nome} adicionado com sucesso.")
                    subprocess.run(['python3', '/home/facial/encode.py'])
                    #subprocess.run(['python3', '/home/facial/saida.py'])
                    reiniciar_streamlit()



                except Error as e:
                    st.error(f"Erro ao adicionar o cliente: {e}")

def pagina_gerenciar_nao_reconhecidos():
    st.header("Gerenciar Clientes Não Reconhecidos")

    caminho_diretorio_desconhecidos = "/home/files/unrecognized/"
    imagens_nao_reconhecidas = os.listdir(caminho_diretorio_desconhecidos)

    if imagens_nao_reconhecidas:
        for img_file in imagens_nao_reconhecidas:
            st.image(os.path.join(caminho_diretorio_desconhecidos, img_file), caption=img_file)

            if f"nome_{img_file}" not in st.session_state:
                st.session_state[f"nome_{img_file}"] = ""
            if f"cpf_{img_file}" not in st.session_state:
                st.session_state[f"cpf_{img_file}"] = ""
            if f"telefone_{img_file}" not in st.session_state:
                st.session_state[f"telefone_{img_file}"] = ""
            if f"profissao_{img_file}" not in st.session_state:
                st.session_state[f"profissao_{img_file}"] = ""
            if f"vendedor_{img_file}" not in st.session_state:
                st.session_state[f"vendedor_{img_file}"] = ""

            st.subheader(f"Cadastrar Cliente Não Reconhecido ({img_file})")
            st.session_state[f"nome_{img_file}"] = st.text_input(f"Nome para {img_file}", st.session_state[f"nome_{img_file}"])
            st.session_state[f"cpf_{img_file}"] = st.text_input(f"CPF para {img_file}", st.session_state[f"cpf_{img_file}"])
            st.session_state[f"telefone_{img_file}"] = st.text_input(f"Telefone para {img_file}", st.session_state[f"telefone_{img_file}"])
            st.session_state[f"profissao_{img_file}"] = st.text_input(f"Profissão para {img_file}", st.session_state[f"profissao_{img_file}"])
            st.session_state[f"vendedor_{img_file}"] = st.text_input(f"Vendedor para {img_file}", st.session_state[f"vendedor_{img_file}"])

            if st.button(f"Salvar Cadastro {img_file}"):
                nome = st.session_state[f"nome_{img_file}"]
                cpf = st.session_state[f"cpf_{img_file}"]
                telefone = st.session_state[f"telefone_{img_file}"]
                profissao = st.session_state[f"profissao_{img_file}"]
                vendedor = st.session_state[f"vendedor_{img_file}"]

                if nome and cpf and telefone and vendedor:
                    connection = conectar_mariadb()
                    if connection:
                        cliente_id = gerar_cliente_id(connection)
                        img_path = f"/home/files/facialfotos/{cliente_id}.jpg"

                        os.rename(os.path.join(caminho_diretorio_desconhecidos, img_file), img_path)
                        adicionar_cliente_banco(connection, cliente_id, nome, profissao, cpf, telefone, int(time.time()), vendedor)
                        
                        subprocess.run(['python3', '/home/facial/encode.py'])
                       # subprocess.run(['python3', '/home/facial/saida.py'])

                        st.success(f"Cliente {nome} cadastrado com sucesso e encodings sincronizados.")

                        #subprocess.run(['python3', '/home/facial/saida.py'])
                        reiniciar_streamlit()



                        st.session_state[f"nome_{img_file}"] = ""
                        st.session_state[f"cpf_{img_file}"] = ""
                        st.session_state[f"telefone_{img_file}"] = ""
                        st.session_state[f"profissao_{img_file}"] = ""
                        st.session_state[f"vendedor_{img_file}"] = ""

            # Adicionando o botão de exclusão
            if st.button(f"Excluir {img_file}"):
                try:
                    os.remove(os.path.join(caminho_diretorio_desconhecidos, img_file))
                    st.success(f"Imagem {img_file} excluída com sucesso!")
                except Exception as e:
                    st.error(f"Erro ao excluir a imagem {img_file}: {e}")


def adicionar_cliente_banco(connection, cliente_id, nome, profissao, cpf, telefone, ano_cliente, vendedor):
    try:
        cursor = connection.cursor()
        query = """
            INSERT INTO clientes (cliente_id, nome, profissao, cpf, telefone, ano_cliente, vendedor)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (cliente_id, nome, profissao, cpf, telefone, ano_cliente, vendedor))
        connection.commit()
        st.success(f"Cliente {nome} adicionado com sucesso ao banco de dados.")
    except Error as e:
        st.error(f"Erro ao adicionar o cliente no banco de dados: {e}")
    finally:
        if cursor:
            cursor.close()

# Função para conectar ao broker MQTT
def conectar_mqtt():
    client = mqtt.Client()
    try:
        client.connect(broker, 1883, 60)
        return client
    except Exception as e:
        st.error(f"Erro ao conectar ao broker MQTT: {e}")
        return None

# Função para converter objetos Decimal e datetime para tipos serializáveis
def decimal_to_float(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return int(obj.timestamp())  # Converte datetime para timestamp epoch em segundos
    raise TypeError(f'Object of type {type(obj)} is not JSON serializable')

# Função para enviar dados via MQTT em formato JSON
def enviar_mqtt_json(cliente_info, timestamp, ultima_venda=None, total_compras=0):
    client = conectar_mqtt()
    if client:
        mensagem = {
            "cliente_id": cliente_info['cliente_id'],
            "evento": "entrada",
            "nome": cliente_info['nome'],
            "cpf": cliente_info['cpf'],
            "telefone": cliente_info['telefone'],  # Telefone do cliente
            "profissao": cliente_info['profissao'],
            "vendedor": cliente_info.get('vendedor', 'N/A'),  # Inclui o vendedor (com valor padrão 'N/A' se não houver)
            "total_compras": total_compras,
            "valor_ultima_compra": ultima_venda['valor_total'] if ultima_venda else 0,
            "data_ultima_compra": decimal_to_float(ultima_venda['ultima_venda']) if ultima_venda else None,
            "itens_comprados": ultima_venda['itens'] if ultima_venda else [],
            "data_ultima_visita": timestamp  # Data da última visita
        }
        mensagem_json = json.dumps(mensagem, default=decimal_to_float)
        client.publish(topico, mensagem_json)
        client.disconnect()


# Função para salvar a imagem detectada (apenas o rosto)
def salvar_imagem_detectada(frame, faceLocs, cliente_id, fator_redimensionamento=2):
    try:
        caminho_diretorio_detected = "/home/files/detected/"
        if not os.path.exists(caminho_diretorio_detected):
            os.makedirs(caminho_diretorio_detected)

        # Usar faceLocs para recortar o rosto e salvar apenas essa parte da imagem
        for (top, right, bottom, left) in faceLocs:
            top *= fator_redimensionamento
            right *= fator_redimensionamento
            bottom *= fator_redimensionamento
            left *= fator_redimensionamento

            # Recorta o rosto da imagem original usando as coordenadas ajustadas
            face_capturada = frame[top:bottom, left:right]

            # Salvar a imagem recortada do rosto
            caminho_imagem_detectada = f"{caminho_diretorio_detected}{cliente_id}.jpg"
            cv2.imwrite(caminho_imagem_detectada, face_capturada)

        return caminho_imagem_detectada
    except Exception as e:
        st.error(f"Erro ao salvar a imagem detectada: {e}")
        return None


def pagina_leituras():
    st.header("LEITURAS ENTRADA")

    connection = conectar_mariadb()
    if connection:
        encodeListKnown, clienteIds = carregar_encodings_do_banco(connection)

    cap = cv2.VideoCapture(stream)

    if not cap.isOpened():
        st.error("Erro ao abrir a câmera.")
        return

    frameST = st.empty()
    detected_clients = {}
    ultima_lista_atualizada = datetime.now()

    intervalo_de_frame = intervalo_frame
    ultimo_frame_processado = time.time()

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            st.error("Erro ao capturar vídeo.")
            break

        if time.time() - ultimo_frame_processado > intervalo_de_frame:
            imgS = cv2.resize(frame, (0, 0), None, 0.5, 0.5)  # Redimensiona para 50%
            imgS = cv2.cvtColor(imgS, cv2.COLOR_BGR2RGB)

            faceLocs = face_recognition.face_locations(imgS, model="hog")
            encodesCurFrame = face_recognition.face_encodings(imgS, faceLocs)

            agora = datetime.now()
            agora_gmt3 = ajustar_para_gmt3(agora)  # Ajuste para GMT-3
            tempo_desde_ultima_atualizacao = (agora - ultima_lista_atualizada).seconds

            if tempo_desde_ultima_atualizacao >= 5:
                for encodeFace, faceLoc in zip(encodesCurFrame, faceLocs):
                    # Se o banco de dados estiver vazio ou não houver rostos conhecidos, apenas salvar como não reconhecido
                    if not encodeListKnown:
                        st.warning("Nenhum rosto conhecido no banco de dados. Salvando como não reconhecido.")
                        salvar_imagem_nao_reconhecida(frame, faceLocs, fator_redimensionamento=2)
                        st.warning("Cliente não reconhecido! Imagem salva para análise.")
                        continue

                    matches = face_recognition.compare_faces(encodeListKnown, encodeFace)
                    faceDis = face_recognition.face_distance(encodeListKnown, encodeFace)

                    if len(faceDis) > 0:  # Garantir que existe algum valor
                        matchIndex = np.argmin(faceDis)

                        if matches[matchIndex]:
                            cliente_id = clienteIds[matchIndex]

                            cliente_info = buscar_informacoes_cliente(connection, cliente_id)

                            if cliente_info:
                                timestamp = int(agora_gmt3.timestamp())  # Convertendo para timestamp ajustado para GMT-3

                                # Envia as informações via MQTT
                                enviar_mqtt_json(cliente_info, timestamp)

                                # Salva apenas o rosto detectado
                                img_detectada_path = salvar_imagem_detectada(frame, [faceLoc], cliente_id, fator_redimensionamento=2)

                                if cliente_id not in detected_clients:
                                    registrar_log_acesso(connection, cliente_id, 'entrada')

                                detected_clients[cliente_id] = agora

                                st.subheader(f"Cliente Detectado: {cliente_info['nome']}")
                                st.write(f"CPF: {cliente_info['cpf']}")
                                st.write(f"Telefone: {cliente_info['telefone']}")
                                st.write(f"Última Visita: {datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')}")

                                exibir_fotos_lado_a_lado(img_detectada_path, cliente_id)

                        else:
                            # Rosto não reconhecido, salvar na pasta unrecognized
                            salvar_imagem_nao_reconhecida(frame, faceLocs, fator_redimensionamento=2)
                            st.warning("Cliente não reconhecido! Imagem salva para análise.")

                    else:
                        # Nenhum rosto conhecido foi encontrado para comparação, salvar como não reconhecido
                        salvar_imagem_nao_reconhecida(frame, faceLocs, fator_redimensionamento=2)
                        st.warning("Cliente não reconhecido! Imagem salva para análise.")

                ultima_lista_atualizada = datetime.now()

            frameST.image(frame, channels="BGR")
            ultimo_frame_processado = time.time()



# Função para salvar a imagem detectada (apenas o rosto)
def salvar_imagem_detectada(frame, faceLocs, cliente_id, fator_redimensionamento=2):
    try:
        caminho_diretorio_detected = "/home/files/detected/"
        if not os.path.exists(caminho_diretorio_detected):
            os.makedirs(caminho_diretorio_detected)

        # Usar faceLocs para recortar o rosto e salvar apenas essa parte da imagem
        for (top, right, bottom, left) in faceLocs:
            # Recorta o rosto da imagem original usando as coordenadas ajustadas
            face_capturada = recortar_face(frame, (top, right, bottom, left), fator_redimensionamento)

            # Salvar a imagem recortada do rosto
            caminho_imagem_detectada = f"{caminho_diretorio_detected}{cliente_id}.jpg"
            cv2.imwrite(caminho_imagem_detectada, face_capturada)

        return caminho_imagem_detectada
    except Exception as e:
        st.error(f"Erro ao salvar a imagem detectada: {e}")
        return None

# Função para salvar imagens não reconhecidas (somente o rosto)
def salvar_imagem_nao_reconhecida(frame, faceLocs, fator_redimensionamento=2):
    try:
        caminho_diretorio_nao_reconhecidos = "/home/files/unrecognized/"
        if not os.path.exists(caminho_diretorio_nao_reconhecidos):
            os.makedirs(caminho_diretorio_nao_reconhecidos)

        timestamp = int(time.time())

        # Ajustar as coordenadas de faceLocs de volta para o tamanho original
        for (top, right, bottom, left) in faceLocs:
            # Recorta o rosto da imagem original usando as coordenadas ajustadas
            face_capturada = recortar_face(frame, (top, right, bottom, left), fator_redimensionamento)

            # Salvar o rosto recortado
            caminho_imagem_nao_reconhecida = f"{caminho_diretorio_nao_reconhecidos}desconhecido_{timestamp}.jpg"
            cv2.imwrite(caminho_imagem_nao_reconhecida, face_capturada)

        return True
    except Exception as e:
        st.error(f"Erro ao salvar a imagem não reconhecida: {e}")
        return False


# Função para exibir o rosto reconhecido e do banco de dados lado a lado
def exibir_fotos_lado_a_lado(img_detectada_path, cliente_id, fator_redimensionamento=1):
    img_detectada = cv2.imread(img_detectada_path)
    
    if img_detectada is None:
        st.error(f"Erro ao carregar a imagem detectada: {img_detectada_path}")
        return
    
    img_detectada_rgb = cv2.cvtColor(img_detectada, cv2.COLOR_BGR2RGB)

    # Verifica se o rosto foi localizado corretamente
    face_locations = face_recognition.face_locations(img_detectada_rgb)
    if face_locations:
        face_capturada = recortar_face(img_detectada_rgb, face_locations[0], fator_redimensionamento)
    else:
        st.warning("Não foi possível detectar o rosto na imagem capturada.")
        face_capturada = img_detectada_rgb  # Exibe a imagem inteira se não encontrar o rosto

    img_banco_path = f"/home/files/facialfotos/{cliente_id}.jpg"
    if os.path.exists(img_banco_path):
        img_banco = cv2.imread(img_banco_path)
        img_banco_rgb = cv2.cvtColor(img_banco, cv2.COLOR_BGR2RGB)
    else:
        img_banco_rgb = np.zeros((100, 100, 3))  # Placeholder caso não exista a imagem

    col1, col2 = st.columns(2)
    with col1:
        st.image(face_capturada, caption="Rosto Capturado", use_column_width=True)
    with col2:
        st.image(img_banco_rgb, caption="Rosto do Banco de Dados", use_column_width=True)

# Função para recortar o rosto de uma imagem
def recortar_face(image, face_location, fator_redimensionamento=1):
    """
    Recorta o rosto da imagem original com base nas coordenadas detectadas,
    ajustando para o fator de redimensionamento.
    """
    top, right, bottom, left = face_location
    # Ajustar as coordenadas de acordo com o fator de redimensionamento
    top = int(top * fator_redimensionamento)
    right = int(right * fator_redimensionamento)
    bottom = int(bottom * fator_redimensionamento)
    left = int(left * fator_redimensionamento)
    return image[top:bottom, left:right]




def pagina_vazia():
    st.write("")  # Simplesmente não faz nada, limpando a tela

#-------------------------------------------------------------
# Menu lateral para alternar entre páginas
menu = st.sidebar.selectbox("Menu", ["Leituras", "Adicionar Cliente", "Gerenciar Não Reconhecidos"])
# Exibir a página correta com base na seleção do menu
if menu == "Leituras":
    pagina_vazia()

    pagina_leituras()
elif menu == "Adicionar Cliente":
    pagina_adicionar_cliente()
elif menu == "Gerenciar Não Reconhecidos":

    pagina_vazia()

    pagina_gerenciar_nao_reconhecidos()
