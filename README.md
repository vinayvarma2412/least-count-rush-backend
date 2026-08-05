# Least Count Rush Backend

A real-time multiplayer backend server for the Least Count Rush card game built with Python FastAPI and WebSockets.

## Game Overview

Least Count Rush is a strategic card game where 2-6 players compete to finish with the lowest score possible.

## Technology Stack

- **Backend**: Python 3.10+ with FastAPI
- **Real-time**: FastAPI WebSockets
- **Validation**: Pydantic models
- **Frontend Testing**: Vanilla HTML/CSS/JavaScript

## Project Structure

```
/least-count-rush-backend
├── app/                    # Backend application
│   ├── main.py            # FastAPI app entry point
│   ├── config.py          # Configuration
│   ├── models/            # Database models
│   ├── schemas/           # Pydantic schemas
│   ├── api/               # API routes and WebSocket handlers
│   ├── services/          # Business logic
│   └── utils/             # Helper functions
├── frontend/              # Testing demo web app
│   ├── index.html         # Main page
│   ├── styles.css         # Styling
│   └── stages/            # Stage-specific UIs
├── requirements.txt       # Python dependencies
└── README.md             # This file
```

## Setup Instructions

### Prerequisites

- Python 3.10 or higher
- pip (Python package manager)

### Installation

1. Create a virtual environment:
```bash
python3 -m venv venv
```

2. Activate the virtual environment:
```bash
# On macOS/Linux:
source venv/bin/activate

# On Windows:
venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Run the server:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The server will be available at `http://localhost:8000`

- API Documentation: `http://localhost:8000/docs`
- Frontend Testing UI: `http://localhost:8000/frontend/index.html`

## Development Stages

The project is developed in incremental, testable stages:

1. **Stage 1**: Project Setup & Basic Server
2. **Stage 2**: Room Management (REST API)
3. **Stage 3**: WebSocket Connection & Room Events
4. **Stage 4**: Card Deck Utilities
5. **Stage 5**: Game Initialization
6. **Stage 6**: Turn Management & Basic Actions
7. **Stage 7**: Declaration System
8. **Stage 8**: Game End & Winner Calculation
9. **Stage 9**: Database Integration (Optional)
10. **Stage 10**: Error Handling & Polish

Each stage includes both backend implementation and a frontend testing UI.

## API Endpoints

### REST API
- `GET /` - Root endpoint
- `GET /health` - Health check
- `POST /api/rooms` - Create a room
- `GET /api/rooms/{room_id}` - Get room details
- `GET /api/rooms` - List all rooms

### WebSocket
- `WS /ws/{room_id}` - WebSocket connection for real-time game updates

## Testing

Each stage includes a frontend testing UI accessible through the web interface. Open the frontend in your browser to test the current stage's functionality.

## License

MIT

