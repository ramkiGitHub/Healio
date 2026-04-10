# Deployment Guide

Complete guide for deploying Healio using Docker and to cloud platforms.

## Table of Contents

1. [Docker Build & Run](#docker-build--run)
2. [Docker Compose (Local)](#docker-compose-local)
3. [Environment Setup](#environment-setup)
4. [Cloud Deployment](#cloud-deployment)
5. [Monitoring & Troubleshooting](#monitoring--troubleshooting)

---

## Docker Build & Run

### Build the Image

```bash
# Build the image locally
docker build -t healio:latest .

# Tag for registry (e.g., Docker Hub)
docker tag healio:latest YOUR_REGISTRY/healio:latest
```

### Run as a Container

```bash
# Run with environment variables
docker run \
  -p 8000:8000 \
  -e OPENAI_API_KEY="sk-proj-xyz..." \
  -e LANGCHAIN_API_KEY="lsv2_..." \
  -e TELEGRAM_BOT_TOKEN="123:ABC" \
  -e DOCTOR_CHAT_ID="456" \
  -e APP_ENV="production" \
  -e LOG_LEVEL="INFO" \
  -v healio-db:/app/data/db \
  healio:latest
```

### Environment Variables Reference

**Required:**
- `OPENAI_API_KEY` — OpenAI secret key
- `LANGCHAIN_API_KEY` — LangSmith API key (for tracing)
- `TELEGRAM_BOT_TOKEN` — Telegram bot token
- `DOCTOR_CHAT_ID` — Doctor's Telegram chat ID

**Optional:**
- `APP_ENV` — "development" or "production" (default: "development")
- `LOG_LEVEL` — "DEBUG", "INFO", "WARNING", "ERROR" (default: "INFO")
- `LANGCHAIN_TRACING_V2` — "true" or "false" (default: "true")
- `DATABASE_URL` — SQLite or PostgreSQL connection string
- `CALENDAR_PROVIDER` — "mock" or "google" (default: "mock")

---

## Docker Compose (Local)

For local multi-container development (includes optional services):

### 1. Create `.env` for Docker

```bash
# Copy and configure
cp .env.example .env

# Edit with your actual credentials:
nano .env
```

### 2. Start All Services

```bash
# Start in foreground (see logs)
docker-compose up

# Or run in background
docker-compose up -d

# View logs
docker-compose logs -f healio
```

### 3. Access Services

| Service | URL |
|---------|-----|
| **Healio API** | http://localhost:8000 |
| **Health Check** | http://localhost:8000/health |
| **API Docs** | http://localhost:8000/docs |
| **Database** | `./data/db/healio.db` (volume mounted) |

### 4. Stop Services

```bash
# Stop running containers
docker-compose down

# Remove containers and volumes
docker-compose down -v
```

---

## Environment Setup

### Production Checklist

- [ ] Set `APP_ENV=production` in environment
- [ ] Set `LOG_LEVEL=WARNING` (reduces log verbosity)
- [ ] Disable interactive docs: `/docs` endpoint disabled automatically
- [ ] HTTPS enabled (use reverse proxy like Nginx or cloud load balancer)
- [ ] Health checks configured (`GET /health`)
- [ ] Resource limits set (CPU, memory)
- [ ] Persistent database (PostgreSQL recommended)
- [ ] Log aggregation configured (e.g., Cloud Logging, ELK)
- [ ] Error monitoring configured (e.g., Sentry)
- [ ] Rate limiting enabled at load balancer
- [ ] Backup strategy for SQLite/PostgreSQL

### Production Environment Variables

```bash
# Application
APP_ENV=production
LOG_LEVEL=WARNING

# OpenAI
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini  # or gpt-4o for higher quality
OPENAI_MAX_TOKENS=512
OPENAI_TEMPERATURE=0.2

# LangSmith (optional but recommended)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_...
LANGCHAIN_PROJECT=healio-production

# Telegram
TELEGRAM_BOT_TOKEN=123:ABC...
DOCTOR_CHAT_ID=987654321
ALERT_TIMEOUT_SECONDS=30

# WhatsApp (Twilio or Meta)
WHATSAPP_PROVIDER=twilio
WHATSAPP_ACCOUNT_SID=ACxxx...
WHATSAPP_AUTH_TOKEN=xxx...
WHATSAPP_FROM_NUMBER=+14155238886

# Calendar (optional)
CALENDAR_PROVIDER=mock  # or 'google'

# Database (use PostgreSQL in production)
DATABASE_URL=postgresql+asyncpg://user:password@postgres-host:5432/healio

# BioBERT (optional)
DISABLE_BIOBERT=false
```

---

## Cloud Deployment

### AWS ECS (Recommended)

#### Prerequisites
- AWS Account with ECR and ECS access
- Docker image pushed to ECR

#### 1. Push Image to ECR

```bash
# Create ECR repository
aws ecr create-repository --repository-name healio

# Get login token
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com

# Tag and push image
docker tag healio:latest YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/healio:latest
docker push YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/healio:latest
```

#### 2. Create ECS Task Definition

Create `healio-task-definition.json`:

```json
{
  "family": "healio",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "containerDefinitions": [
    {
      "name": "healio",
      "image": "YOUR_ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/healio:latest",
      "portMappings": [
        {
          "containerPort": 8000,
          "hostPort": 8000,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "APP_ENV",
          "value": "production"
        },
        {
          "name": "LOG_LEVEL",
          "value": "INFO"
        }
      ],
      "secrets": [
        {
          "name": "OPENAI_API_KEY",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:YOUR_ACCOUNT:secret:healio/openai-key"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/healio",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": [
          "CMD-SHELL",
          "curl -f http://localhost:8000/health || exit 1"
        ],
        "interval": 30,
        "timeout": 5,
        "retries": 3
      }
    }
  ]
}
```

#### 3. Register and Run

```bash
# Register task definition
aws ecs register-task-definition --cli-input-json file://healio-task-definition.json

# Run task in ECS cluster
aws ecs run-task \
  --cluster healio-cluster \
  --task-definition healio:1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=ENABLED}"
```

### Google Cloud Run

#### 1. Build and Push to Artifact Registry

```bash
# Configure Docker
gcloud auth configure-docker us-central1-docker.pkg.dev

# Build image
docker build -t us-central1-docker.pkg.dev/YOUR_PROJECT/healio/app:latest .

# Push to Artifact Registry
docker push us-central1-docker.pkg.dev/YOUR_PROJECT/healio/app:latest
```

#### 2. Deploy to Cloud Run

```bash
gcloud run deploy healio \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT/healio/app:latest \
  --platform managed \
  --region us-central1 \
  --memory 1Gi \
  --cpu 1 \
  --timeout 3600 \
  --set-env-vars "APP_ENV=production,LOG_LEVEL=INFO" \
  --set-secrets "OPENAI_API_KEY=healio-openai-key:latest,LANGCHAIN_API_KEY=healio-langsmith-key:latest" \
  --allow-unauthenticated
```

### Azure Container Instances

```bash
# Push to ACR
az acr build --registry healioregistry --image healio:latest .

# Deploy to ACI
az container create \
  --resource-group healio-rg \
  --name healio-api \
  --image healioregistry.azurecr.io/healio:latest \
  --cpu 1 \
  --memory 1 \
  --ports 8000 \
  --environment-variables \
    APP_ENV=production \
    LOG_LEVEL=INFO \
  --registry-login-server healioregistry.azurecr.io \
  --registry-username USERNAME \
  --registry-password PASSWORD
```

---

## Monitoring & Troubleshooting

### Health Checks

```bash
# Simple liveness check
curl http://localhost:8000/health

# Expected response:
# {"status": "ok", "version": "0.1.0", "env": "production"}
```

### Viewing Logs

#### Docker Container
```bash
docker logs -f <container_id>
```

#### Cloud Logging (Google Cloud)
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=healio" --limit 50
```

#### AWS CloudWatch
```bash
aws logs tail /ecs/healio --follow
```

### Performance Tuning

**CPU/Memory Settings:**
- Small clinic (< 100 patients/day): 512 MB RAM, 0.25 CPU
- Mid clinic (100-500 patients/day): 1 GB RAM, 0.5 CPU
- Large clinic (> 500 patients/day): 2 GB RAM, 1 CPU

**Database:**
- SQLite: Good for single-instance deployments
- PostgreSQL: Recommended for multi-instance/production (supports concurrent connections)

### Scaling

**Horizontal Scaling:**
- Deploy multiple instances behind a load balancer
- Use managed databases (RDS, CloudSQL) instead of local SQLite
- Session state is persisted in LangGraph checkpoint store

**Vertical Scaling:**
- Increase CPU and memory allocations
- Use larger OpenAI models (gpt-4o) for better quality

### Common Issues

#### Container exits immediately
```bash
docker logs <container_id>
# Check for missing environment variables or .env file issues
```

#### Database locked error
```bash
# Switch to PostgreSQL if running multiple instances
DATABASE_URL=postgresql+asyncpg://user:password@host/healio
```

#### High latency on LLM responses
```bash
# Check LangSmith traces at https://smith.langchain.com
# May indicate:
# - Rate limiting from OpenAI
# - Network latency
# - BioBERT model loading time (only first request)
```

### Backup Strategy

**For SQLite:**
```bash
# Backup database daily
docker cp healio-container:/app/data/db/healio.db ./backups/healio-$(date +%Y%m%d).db
```

**For PostgreSQL:**
```bash
pg_dump -h host -U user -d healio > backups/healio-$(date +%Y%m%d).sql
```

---

## Next Steps

- See [LOCAL_SETUP.md](./LOCAL_SETUP.md) for local development
- See [CLINIC_SETUP.md](./CLINIC_SETUP.md) for clinic deployment checklist
