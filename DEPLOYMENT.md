# Deployment Guide

This document covers various deployment options for the Nordic Company Agent.

## Table of Contents

- [GitHub Pages (Static UI Demo)](#github-pages-static-ui-demo)
- [Docker Deployment](#docker-deployment)
- [Cloud Platforms](#cloud-platforms)
- [Self-Hosted](#self-hosted)
- [Environment Variables](#environment-variables)

---

## GitHub Pages (Static UI Demo)

**Live Demo:** https://coder-nk.github.io/nordic-company-agent/

The static UI demo is automatically deployed to GitHub Pages and includes:
- Landing page with feature highlights
- Interactive demo with 100 sample companies
- Company detail pages with full evidence trails

### Deploying Your Own GitHub Pages Instance

```bash
# Generate static site from your data
uv run nca run --input your-data.jsonl --out out/your-run

# The static site is automatically generated in out/your-run/site/
# Copy to docs/ directory for GitHub Pages
cp -r out/your-run/site/* docs/

# Commit and push
git add docs/
git commit -m "Update GitHub Pages with latest data"
git push origin gh-pages
```

---

## Docker Deployment

### Quick Start with Docker Compose

```bash
# Start both UI and API servers
docker-compose up -d

# Access the services
# UI: http://localhost:8765
# API: http://localhost:8080
```

### Build Docker Image

```bash
# Build the image
docker build -t nordic-company-agent .

# Run interactive UI
docker run -p 8765:8765 -v $(pwd)/out:/app/out nordic-company-agent ui --port 8765 --host 0.0.0.0

# Run API server
docker run -p 8080:8080 -v $(pwd)/out:/app/out nordic-company-agent serve --port 8080 --host 0.0.0.0

# Run batch processing
docker run -v $(pwd)/batches:/data -v $(pwd)/out:/app/out \
  nordic-company-agent run --input /data/companies.jsonl --out /app/out/batch
```

### Docker Compose Services

The `docker-compose.yml` includes three services:

1. **UI Server** (port 8765) - Interactive web interface
2. **API Server** (port 8080) - HTTP API for company research
3. **Worker** (commented out) - Background batch processing

---

## Cloud Platforms

### Deploy to Heroku

```bash
# Install Heroku CLI and login
heroku login

# Create app
heroku create your-app-name

# Add Python buildpack
heroku buildpacks:set heroku/python

# Deploy
git push heroku master

# Start web process
heroku ps:scale web=1
```

Create a `Procfile`:
```
web: nca ui --port $PORT --host 0.0.0.0
api: nca serve --port $PORT --host 0.0.0.0
```

### Deploy to Google Cloud Run

```bash
# Build and push to Google Container Registry
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/nordic-company-agent

# Deploy to Cloud Run
gcloud run deploy nordic-company-agent \
  --image gcr.io/YOUR_PROJECT_ID/nordic-company-agent \
  --platform managed \
  --region europe-north1 \
  --allow-unauthenticated \
  --port 8765 \
  --command nca \
  --args ui,--port,8765,--host,0.0.0.0
```

### Deploy to AWS (ECS)

```bash
# Build and push to Amazon ECR
aws ecr create-repository --repository-name nordic-company-agent
aws ecr get-login-password --region eu-north-1 | docker login --username AWS --password-stdin YOUR_ACCOUNT.dkr.ecr.eu-north-1.amazonaws.com

docker tag nordic-company-agent:latest YOUR_ACCOUNT.dkr.ecr.eu-north-1.amazonaws.com/nordic-company-agent:latest
docker push YOUR_ACCOUNT.dkr.ecr.eu-north-1.amazonaws.com/nordic-company-agent:latest

# Create ECS task definition and service (see AWS documentation)
```

### Deploy to Azure Container Instances

```bash
# Create resource group
az group create --name nordic-company-agent-rg --location norwayeast

# Create container instance
az container create \
  --resource-group nordic-company-agent-rg \
  --name nordic-company-agent \
  --image YOUR_REGISTRY/nordic-company-agent:latest \
  --dns-name-label nordic-company-agent \
  --ports 8765 \
  --environment-variables PYTHONUNBUFFERED=1 \
  --command-line "nca ui --port 8765 --host 0.0.0.0"
```

### Deploy to DigitalOcean App Platform

1. Fork the repository
2. Go to DigitalOcean App Platform
3. Create new app from GitHub repo
4. Configure:
   - **Type:** Web Service
   - **Build Command:** `pip install -r requirements.txt && pip install --no-deps -e .`
   - **Run Command:** `nca ui --port 8080 --host 0.0.0.0`
   - **HTTP Port:** 8080

---

## Self-Hosted

### System Requirements

- Python 3.12+
- 2GB RAM minimum (4GB recommended)
- 10GB disk space for caches and outputs
- Linux/macOS/Windows with WSL

### Installation

```bash
# Clone repository
git clone https://github.com/coder-nk/nordic-company-agent.git
cd nordic-company-agent

# Install with uv (recommended)
uv sync

# Or with pip
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install --no-deps -e .
```

### Run as Systemd Service (Linux)

Create `/etc/systemd/system/nordic-company-agent-ui.service`:

```ini
[Unit]
Description=Nordic Company Agent UI
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/nordic-company-agent
Environment="PATH=/opt/nordic-company-agent/.venv/bin"
ExecStart=/opt/nordic-company-agent/.venv/bin/nca ui --port 8765 --host 0.0.0.0
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable nordic-company-agent-ui
sudo systemctl start nordic-company-agent-ui
sudo systemctl status nordic-company-agent-ui
```

### Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support (if needed)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

---

## Environment Variables

Configure the agent using environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `NCA_MAX_REQUESTS` | 3000 | Maximum HTTP requests per batch |
| `NCA_TIME_BUDGET_S` | 2400 | Time budget in seconds (40 minutes) |
| `PYTHONUNBUFFERED` | - | Set to 1 for real-time logging |

### Example Usage

```bash
# Local development
export NCA_MAX_REQUESTS=5000
export NCA_TIME_BUDGET_S=3600
./run.sh batches/large-batch.jsonl out/large

# Docker
docker run -e NCA_MAX_REQUESTS=5000 -e NCA_TIME_BUDGET_S=3600 \
  nordic-company-agent run --input /data/batch.jsonl --out /app/out/batch
```

---

## Production Best Practices

### Security

1. **Use HTTPS:** Always deploy behind SSL/TLS in production
2. **Rate Limiting:** Implement rate limiting on the API endpoints
3. **Access Control:** Add authentication for sensitive deployments
4. **Input Validation:** The agent validates org numbers, but add additional checks at the API layer

### Performance

1. **Concurrency:** Adjust `--concurrency` flag based on available CPU cores
2. **Caching:** Mount cache volumes persistently to avoid re-downloading
3. **Resource Limits:** Set appropriate memory/CPU limits in Docker/K8s
4. **Monitoring:** Track request counts, response times, and error rates

### Monitoring

Example Prometheus metrics endpoint (requires custom implementation):

```python
# Add to server.py
from prometheus_client import Counter, Histogram, generate_latest

request_count = Counter('nca_requests_total', 'Total requests')
request_duration = Histogram('nca_request_duration_seconds', 'Request duration')

# Expose metrics at /metrics
```

### Backup & Recovery

```bash
# Backup critical data
tar -czf backup-$(date +%Y%m%d).tar.gz \
  out/*/envelopes.jsonl \
  out/*/run-report.json \
  cache/

# Restore
tar -xzf backup-YYYYMMDD.tar.gz
```

---

## Scaling

### Horizontal Scaling

Run multiple worker containers for batch processing:

```yaml
# docker-compose.yml
services:
  worker-1:
    build: .
    command: run --input /data/batch1.jsonl --out /app/out/batch1
    
  worker-2:
    build: .
    command: run --input /data/batch2.jsonl --out /app/out/batch2
```

### Kubernetes

Example deployment:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: nordic-company-agent
spec:
  replicas: 3
  selector:
    matchLabels:
      app: nordic-company-agent
  template:
    metadata:
      labels:
        app: nordic-company-agent
    spec:
      containers:
      - name: ui
        image: nordic-company-agent:latest
        command: ["nca", "ui", "--port", "8765", "--host", "0.0.0.0"]
        ports:
        - containerPort: 8765
        resources:
          requests:
            memory: "2Gi"
            cpu: "1000m"
          limits:
            memory: "4Gi"
            cpu: "2000m"
        volumeMounts:
        - name: data
          mountPath: /app/out
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: nordic-company-agent-data
```

---

## Troubleshooting

### Common Issues

**Issue:** Port already in use
```bash
# Find and kill process
lsof -ti:8765 | xargs kill -9
```

**Issue:** Out of memory during large batches
```bash
# Reduce concurrency
nca run --input batch.jsonl --out out/batch --concurrency 12

# Or split into smaller batches
split -l 100 large-batch.jsonl batch-
```

**Issue:** Docker container exits immediately
```bash
# Check logs
docker logs container-name

# Run interactively
docker run -it nordic-company-agent /bin/bash
```

---

## Support

- **GitHub Issues:** https://github.com/coder-nk/nordic-company-agent/issues
- **Documentation:** See README.md, DESIGN.md, and other .md files
- **Tests:** Run `uv run pytest -q` to verify installation

---

## License

MIT License - See LICENSE file for details.
