import os
import cv2
import face_recognition
import pickle
import mysql.connector
from mysql.connector import Error

# Conectar ao banco de dados MariaDB
def conectar_mariadb():
    try:
        connection = mysql.connector.connect(
            host='localhost',
            database='mendes',  # Substitua pelo nome do seu banco de dados
            user='seu_usuario_mysql',  # Substitua pelo seu nome de usuário
            password='sua_senha_mysql'  # Substitua pela sua senha
        )
        if connection.is_connected():
            print("Conectado ao MariaDB")
            return connection
    except Error as e:
        print(f"Erro ao conectar ao MariaDB: {e}")
        return None

def findEncodings(imagesList):
    """Função para codificar as imagens da lista de imagens."""
    encodeList = []
    for img in imagesList:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        encodings = face_recognition.face_encodings(img)
        
        # Verifica se há algum rosto na imagem
        if len(encodings) > 0:
            encode = encodings[0]
            encodeList.append(encode)
        else:
            print("Nenhum rosto encontrado na imagem. Pulando esta imagem.")
    return encodeList

def inserir_encodings_no_banco(connection, clienteIds, encodeListKnown, pathList):
    """Função para inserir encodings e IDs no banco de dados."""
    try:
        cursor = connection.cursor()
        for i in range(len(clienteIds)):
            cliente_id = clienteIds[i]
            encoding = pickle.dumps(encodeListKnown[i])  # Converte o encoding para binário
            imagem_nome = pathList[i]

            cursor.execute("""
                INSERT INTO encodings (cliente_id, encoding, imagem_nome) 
                VALUES (%s, %s, %s)
            """, (cliente_id, encoding, imagem_nome))

        connection.commit()
        print("Encodings inseridos no banco de dados com sucesso.")
    except Error as e:
        print(f"Erro ao inserir encodings no banco de dados: {e}")
    finally:
        if cursor:
            cursor.close()

def main():
    connection = conectar_mariadb()
    if connection is None:
        return

    # Verifica se a pasta de imagens existe
    folderPath = '/home/files/facialfotos'
    if not os.path.exists(folderPath):
        print(f"Pasta {folderPath} não encontrada.")
        return
    
    pathList = os.listdir(folderPath)
    print("Lista de arquivos na pasta:", pathList)

    if len(pathList) == 0:
        print("Nenhuma imagem encontrada na pasta.")
        return

    imgList = []
    clienteIds = []

    # Carregar as imagens da pasta e os IDs de clientes
    for path in pathList:
        imgList.append(cv2.imread(os.path.join(folderPath, path)))
        clienteIds.append(os.path.splitext(path)[0])  # Nome do arquivo sem extensão será o ID do cliente

    print("Iniciando a codificação das imagens ...")
    encodeListKnown = findEncodings(imgList)
    print("Codificação completa")

    # Inserir os encodings e IDs no banco de dados
    if len(encodeListKnown) > 0:
        inserir_encodings_no_banco(connection, clienteIds, encodeListKnown, pathList)
    else:
        print("Nenhum encoding para inserir no banco de dados.")

    if connection.is_connected():
        connection.close()
        print("Conexão ao MariaDB encerrada.")

if __name__ == "__main__":
    main()
