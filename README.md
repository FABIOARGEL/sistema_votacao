# Sistema de Votação

Este é um projeto de Sistema de Votação desenvolvido em Python.

## Estrutura do Projeto

- `app.py`: Arquivo principal da aplicação.
- `config.py`: Configurações do projeto.
- `core/`: Lógica central do negócio.
- `models/`: Modelos de dados.
- `routes/`: Rotas da aplicação web.
- `socket_handlers/`: Manipuladores de WebSockets.
- `static/`: Arquivos estáticos (CSS, JS, imagens).
- `templates/`: Templates HTML.

## Pré-requisitos

- Python 3.8+
- (Recomendado) Ambiente virtual (venv)

## Instalação

1. Clone o repositório:
```bash
git clone <url-do-repositorio>
cd sistema_votacao
```

2. Crie e ative um ambiente virtual:
```bash
python -m venv .venv
# No Windows:
.venv\Scripts\activate
# No Linux/Mac:
source .venv/bin/activate
```

3. Instale as dependências:
```bash
pip install -r requirements.txt
```

4. Configure as variáveis de ambiente:
Copie o arquivo `.env.example` para `.env` e ajuste as configurações conforme necessário:

```bash
# No Linux/Mac:
cp .env.example .env

# No Windows:
copy .env.example .env
```

Exemplo de conteúdo do arquivo `.env.example`:
```env
GOOGLE_CLIENT_ID=seu_google_client_id_aqui
GOOGLE_CLIENT_SECRET=seu_google_client_secret_aqui
SECRET_KEY=mude-esta-chave-secreta-em-producao
MONGO_URI=mongodb://localhost:27017/
MONGO_DBNAME=sistema_votacao
```

## Execução

Certifique-se de que o **MongoDB** esteja em execução na sua máquina (ou ajuste a `MONGO_URI` no arquivo `.env` para o seu banco de dados).

Para iniciar a aplicação, execute o arquivo principal:
```bash
python app.py
```

Em seguida, acesse o sistema pelo seu navegador no endereço: `http://localhost:5000` (ou na porta indicada no terminal).

## Tecnologias Utilizadas

- Python
- (Outras tecnologias como Flask/FastAPI, Socket.IO, Banco de Dados, etc. baseados nos pacotes instalados)
