# Sensedia Insurance API

A Flask-based REST API that provides insurance-related services, including gender inference based on titles and health monitoring.

## Features

- RESTful API endpoints using Flask and Flask-RESTX
- Automatic Swagger/OpenAPI documentation
- Gender inference from titles
- Health check endpoint
- Configurable logging with rotation
- Environment-based configuration
- CORS support
- Docker support with volume persistence

## Project Structure

```plaintext
sensedia-insurance/
├── app.py              # Main application file
├── requirements.txt    # Python dependencies
├── .env               # Environment configuration
├── .env.example       # Example environment configuration
├── .gitignore         # Git ignore rules
├── README.md          # Project documentation
├── Dockerfile         # Docker build instructions
├── docker-compose.yml # Docker compose configuration
├── data/              # Database storage
│   └── insurance.db   # SQLite database file
└── logs/              # Application logs
    └── app.log        # Main log file
```

## Requirements

### Local Development

- Python 3.10+
- Dependencies listed in `requirements.txt`

### Docker Deployment

- Docker
- Docker Compose

## Setup

1. Clone the repository:
```bash
git clone [your-repository-url]
cd sensedia-insurance
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
.\venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create a `.env` file with required configuration:
```
LOG_LEVEL=INFO
LOG_FILE=app.log
CORS_ORIGINS=*
```

## Getting Started

## Installation and Setup

1. Clone the repository:

```bash
git clone [your-repository-url]
cd sensedia-insurance
```

2. Create and activate a virtual environment:

```bash
python -m venv venv
.\venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/Mac
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Create a `.env` file with required configuration:

```env
DATA_DIR=data
DB_NAME=insurance.db
LOG_LEVEL=INFO
LOGS_PATH=logs
MAX_LOG_FILES=30
CORS_ALLOW_ORIGIN=*
```

5. Start the server:

```bash
python app.py
```

## Container Deployment

1. Build and start the containers:

```bash
docker-compose up -d
```

2. View logs:

```bash
docker-compose logs -f
```

3. Stop the containers:

```bash
docker-compose down
```

### Data Persistence

The Docker configuration includes volume mapping for:

- Database: `./data:/app/data`
- Logs: `./logs:/app/logs`

These directories are automatically created and persist data even if containers are removed.

## API Access

The API will be available at:

- Local: `http://localhost:5000`
- Docker: `http://localhost:5000`

Swagger documentation can be accessed at `/swagger`

## Cloud Deployment — Vercel

This project is also deployed on Vercel and can be reached at:

- https://insurance-quotes-api.vercel.app/

Notes and important considerations when using Vercel:

- Environment variables: configure the same variables you use locally in the Vercel dashboard (Project Settings -> Environment Variables). Examples include `DATA_DIR`, `DB_NAME`, `LOG_LEVEL`, `LOGS_PATH`, `MAX_LOG_FILES`, and `CORS_ALLOW_ORIGIN`.
- Filesystem persistence: Vercel uses an ephemeral filesystem for serverless deployments. This means the local `data/` and `logs/` directories (and any SQLite file inside them) are not a reliable place to store data in production. For persistent storage use a managed database (Postgres, MySQL, or a cloud-hosted SQLite alternative) or an object storage service (S3-compatible) and update `DB_PATH` accordingly.
- Ports and routing: Vercel manages routing for you; the application will be available at the deployed URL. You don't need to set `HOST` or `PORT` in Vercel — these are handled by the platform.
- Build & deployment: if you deploy using the provided `Dockerfile`/`docker-compose.yml`, ensure your Vercel project is configured to use a Docker-based deployment or adjust the project to run as a serverless function/ASGI app if desired.

If you want, I can add a short `vercel.json` or deployment notes with exact build settings for this repository.

### Serverless (Vercel) runtime notes

When running on Vercel (serverless) you must avoid writing to the project filesystem. Use these settings and steps:

 In the Vercel dashboard (Project → Settings → Environment Variables) set these values:
 
 - `SERVERLESS=true`
 - `USE_FILE_LOGS=false`
 - `LOG_LEVEL=INFO`
 - `CORS_ALLOW_ORIGIN=https://insurance-quotes-api.vercel.app`
 - `DATABASE_URL` = set to your Postgres (or other) managed DB connection string (recommended for persistence). Example: `postgres://user:pass@host:5432/dbname`

- Do not rely on `DATA_DIR`/`DB_NAME` pointing to a persistent sqlite file — the filesystem is ephemeral on serverless. If you still use sqlite for quick tests, set `DATA_DIR=/tmp` and accept that data is temporary.

- Ensure `USE_FILE_LOGS` is `false` so the app logs to stdout and Vercel captures logs.

- If you plan to deploy using Docker (container) instead of serverless functions, make sure to set `HOST=0.0.0.0` and `PORT=3000` in Vercel environment variables and enable Docker-based deployment.

Quick checklist for a working Vercel deployment:

1. Set environment variables listed above in Vercel.
2. Add `vercel.json` (included) to configure function memory and timeouts, or use the dashboard settings.
3. Use a managed `DATABASE_URL` for persistence and verify network access from Vercel to the DB.

The application now emits a startup log which prints the active configuration (SERVERLESS, DB in use, logging mode).

How to verify the startup configuration in Vercel logs:

1. In the Vercel dashboard go to your Project → Deployments and open the deployment you want to inspect.
2. Click the "Logs" tab.
3. Search for the string `Starting insurance_app` or `Configuration: SERVERLESS` to find the startup log block.

The startup log includes a sanitized DB display (credentials redacted) and indicates whether the app is running in serverless mode and whether file-based logging is enabled.

## Business Rules

### Gender Inference

The API provides gender inference based on two methods:

1. Title-based inference:
   - Uses predefined lists of male and female titles
   - Supports multiple languages and formats
   - Examples of titles:
     - Male: Sr., Mr., Dr., Prof., Eng., etc.
     - Female: Sra., Mrs., Dra., Profa., Enga., etc.

2. Name-based inference (fallback):
   - Uses genderize.io API for name-based prediction
   - Returns 'M' for male, 'F' for female
   - Defaults to 'M' if unable to determine

### Insurance Quote Calculation

1. Input Validation:
   - Required fields: name, CPF, gender, birth date, capital amount, coverage dates
   - CPF must be 11 digits
   - Gender must be 'M' or 'F'
   - Coverage dates must be valid and in the future
   - Capital amount must be positive

2. Rate Calculation:
   - Base annual rate applies to all quotes
   - Adjusted rate considers:
     - Age of insured person
     - Coverage period
     - Capital amount

3. Premium Calculation:
   - Based on adjusted rate and coverage period
   - Takes into account:
     - Coverage duration in days/years
     - Capital amount
     - Adjusted rate

### Data Storage

- SQLite database with the following structure:
  - Quote ID (auto-generated)
  - Personal information (name, CPF, gender, birth date)
  - Coverage details (start date, end date)
  - Financial data (capital amount, rates, premium)
  - Metadata (creation timestamp)

## API Endpoints

- `GET /health` - Health check endpoint
- `POST /insurance/gender` - Gender inference from title
- Additional endpoints documented in Swagger UI

## Logging

The application uses TimedRotatingFileHandler for log management:

- Log files are stored in the `logs` directory
- Daily rotation with configurable retention
- Separate console and file logging
- Configurable log levels through environment variables

## Development

- Built with Flask-RESTX for API development and documentation
- Environment-based configuration through `.env` file
- CORS enabled and configurable
- Docker support for consistent development and deployment
- SQLite database with configurable path

## License

This project is licensed under the MIT License. See the `LICENSE` file in the project root for the full license text.

Copyright (c) 2025 fnldesign
