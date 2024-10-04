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

import os

# Salvar o PID do processo Streamlit em um arquivo
pid_file_path = "/tmp/saida_streamlit_pid.txt"

# Salvar o PID
with open(pid_file_path, "w") as f:
    f.write(str(os.getpid()))

print(f"PID do Streamlit salvo em {pid_file_path}")

#cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'YUYV'))
# Configurações do MQTT e banco
broker = "datawatcher360.online"
topico = "vasco1/facial"
#stream = "http://root:samtech123@172.16.20.156/video1s3.mjpg"  # VIVOTEK
#stream = "http://datawatcher360.online:5000/live-stream"
stream = 0
hostmysql = "localhost"
#hostmysql = "172.16.20.137"
intervalo_frame = 0.5

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

# Função para registrar log de acesso (entrada) no banco de dados
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

# Função para registrar log de saída no banco de dados
def registrar_log_saida(connection, cliente_id):
    try:
        cursor = connection.cursor()
        query = """
            INSERT INTO log_acessos (cliente_id, tipo_acesso, data_hora)
            VALUES (%s, %s, NOW())
        """
        cursor.execute(query, (cliente_id, 'saida'))
        connection.commit()
    except Error as e:
        st.error(f"Erro ao registrar log de saída: {e}")
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
    if isinstance(obj, datetime):
        return int(obj.timestamp())  # Converte datetime para timestamp epoch em segundos
    raise TypeError(f'Object of type {type(obj)} is not JSON serializable')


# Função para enviar dados de saída via MQTT
def enviar_mqtt_saida(cliente_info, timestamp):
    client = conectar_mqtt()
    if client:
        mensagem = {
            "cliente_id": cliente_info['cliente_id'],
            "nome": cliente_info['nome'],
            "vendedor": cliente_info.get('vendedor', 'N/A'),  # Inclui o vendedor na saída
            "evento": "saida",
            "data_saida": timestamp  # Data da saída
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

# Função para monitorar leituras de clientes detectados
def pagina_leituras():
    st.header("LEITURA SAIDAS")

    connection = conectar_mariadb()
    if connection:
        encodeListKnown, clienteIds = carregar_encodings_do_banco(connection)

    cap = cv2.VideoCapture(stream)
    #cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))

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
                    if not encodeListKnown:
                        continue

                    matches = face_recognition.compare_faces(encodeListKnown, encodeFace)
                    faceDis = face_recognition.face_distance(encodeListKnown, encodeFace)

                    if len(faceDis) > 0:
                        matchIndex = np.argmin(faceDis)

                        if matches[matchIndex]:
                            cliente_id = clienteIds[matchIndex]

                            cliente_info = buscar_informacoes_cliente(connection, cliente_id)

                            if cliente_info:
                                timestamp = int(agora_gmt3.timestamp())  # Convertendo para timestamp ajustado para GMT-3

                                # Envia as informações de entrada via MQTT
                                #enviar_mqtt_json(cliente_info, timestamp)

                                if cliente_id not in detected_clients:
                                    registrar_log_acesso(connection, cliente_id, 'saida')

                                detected_clients[cliente_id] = agora

                                st.subheader(f"Cliente Saiu: {cliente_info['nome']}")
                                st.write(f"CPF: {cliente_info['cpf']}")
                                st.write(f"Telefone: {cliente_info['telefone']}")
                                st.write(f"Vendedor: {cliente_info['vendedor']} EMITIR A OS COM URGENCIA")
                                st.write(f"Última Visita: {datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')}")

                # Verifica a saída de clientes
                clientes_para_saida = []
                for cliente_id, ultima_deteccao in detected_clients.items():
                    if (agora - ultima_deteccao).seconds > 10:  # Considera saída após 10 segundos sem detecção
                        clientes_para_saida.append(cliente_id)
                        timestamp_saida = int(agora_gmt3.timestamp())
                        
                        # Publica a saída via MQTT e registra no banco
                        enviar_mqtt_saida(cliente_info, timestamp_saida)
                        #registrar_log_saida(connection, cliente_id)

                       # st.subheader(f"Cliente Saiu: {cliente_id}")
                        #st.write(f"Data e Hora da Saída: {datetime.fromtimestamp(timestamp_saida).strftime('%Y-%m-%d %H:%M:%S')}")

                # Remove clientes que saíram
                for cliente_id in clientes_para_saida:
                    del detected_clients[cliente_id]

                ultima_lista_atualizada = datetime.now()

            frameST.image(frame, channels="BGR")
            ultimo_frame_processado = time.time()

#-------------------------------------------------------------
# Menu lateral para alternar entre páginas
st.sidebar.header("Monitoramento Facial")
pagina_leituras()
