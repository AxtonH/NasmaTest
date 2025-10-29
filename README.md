# Nasma - AI Assistant

A modular AI assistant application that integrates with ChatGPT and is designed to work with Odoo systems. Built with Flask backend and Tailwind CSS frontend.

## Project Structure

```
NasmaPL/
├── backend/
│   ├── app.py                 # Main Flask application
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py        # Configuration settings
│   └── services/
│       ├── __init__.py
│       └── chatgpt_service.py # ChatGPT integration service
├── frontend/
│   ├── static/
│   │   ├── css/
│   │   │   └── style.css      # Custom styles
│   │   └── js/
│   │       └── chat.js        # Chat functionality
│   └── templates/
│       └── index.html         # Main chat interface
├── requirements.txt           # Python dependencies
└── README.md                 # This file
```

## Features

- **Modular Architecture**: Clean separation of concerns with dedicated modules for services and configuration
- **ChatGPT Integration**: Uses OpenAI's Assistants API for advanced conversational AI
- **Modern UI**: Responsive design with Tailwind CSS
- **Real-time Chat**: Interactive chat interface with typing indicators and status updates
- **Thread Management**: Maintains conversation context across messages
- **Error Handling**: Comprehensive error handling and user feedback
- **Health Monitoring**: API health checks and connection status indicators

## Setup Instructions

### Prerequisites

- Python 3.8 or higher
- pip (Python package manager)

### Installation

1. **Clone or navigate to the project directory**
   ```bash
   cd NasmaPL
   ```

2. **Create a virtual environment (recommended)**
   ```bash
   python -m venv venv
   
   # On Windows
   venv\Scripts\activate
   
   # On macOS/Linux
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Environment Variables**
   
   Copy the provided `.env` file (or create one using `.env` as a template) and update the values:
   - `SECRET_KEY` (already populated for you)
   - `OPENAI_API_KEY` (required)
   - `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD` (as provided by your Odoo instance)
   - Optional debug toggles such as `VERBOSE_LOGS`

5. **Run the application**
   ```bash
   cd backend
   python app.py
   ```

6. **Access the application**
   
   Open your web browser and navigate to: `http://localhost:5000`

## Usage

1. **Start a Conversation**: Type your message in the input field at the bottom of the page
2. **Send Messages**: Press Enter or click the send button to send your message
3. **View Responses**: The ChatGPT assistant will respond to your queries
4. **Clear Chat**: Use the "Clear Chat" button to start a new conversation
5. **Monitor Status**: Check the connection status indicator in the top-right corner

## API Endpoints

- `GET /` - Main chat interface
- `POST /api/chat` - Send message to ChatGPT assistant
- `GET /api/health` - Health check endpoint

## Future Enhancements

This is the initial shell for the chatbot. Future development will include:

- **Odoo Integration**: Connect to Odoo systems for data retrieval and manipulation
- **NLP Flow Detection**: Implement natural language processing to determine appropriate workflows
- **Multiple Flows**: Create various flows for different Odoo operations (read/write data)
- **Authentication**: Add user authentication and session management
- **Database Integration**: Store conversation history and user preferences
- **Advanced Error Handling**: More sophisticated error recovery and user guidance

## Development Notes

- The application uses Flask's development server for local testing
- All configurations are currently hardcoded for simplicity
- The frontend uses CDN-delivered Tailwind CSS for rapid prototyping
- Error handling includes both backend validation and frontend user feedback

## Contributing

This is a modular codebase designed for easy extension. When adding new features:

1. Keep services in the `backend/services/` directory
2. Add new configurations to `backend/config/settings.py`
3. Follow the existing naming conventions and code structure
4. Test all new features thoroughly before integration
