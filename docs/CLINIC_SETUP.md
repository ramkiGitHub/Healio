# Clinic Deployment Setup Checklist

Step-by-step guide for clinics to deploy and configure Healio.

## Pre-Deployment

### Team Preparation
- [ ] Identify system administrator (manages infrastructure)
- [ ] Identify clinic workflows owner (defines patient flows)
- [ ] Identify HIPAA/compliance officer (if required)
- [ ] Schedule staff training on Healio features

### Hardware & Infrastructure
- [ ] Server/cloud VM provisioned (2 GB RAM minimum, 10 GB storage)
- [ ] High-speed internet connection (≥ 10 Mbps)
- [ ] Backup power (UPS for local servers)
- [ ] Redundancy/failover plan in place
- [ ] SSL/TLS certificates available (for HTTPS)

---

## 1. Obtain API Keys

### OpenAI

1. Visit [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys)
2. Click **+ Create new secret key**
3. Name it "Healio Clinic"
4. Copy and save securely (shown only once)
5. Set up billing at [https://platform.openai.com/account/billing/overview](https://platform.openai.com/account/billing/overview)

**Recommended:**
- Budget limit: $100-500/month (configurable)
- Model: `gpt-4o-mini` (cost-effective; upgrade to `gpt-4o` for higher quality)

### Telegram

1. Open Telegram and search for `@BotFather`
2. Send `/start` then `/newbot`
3. Follow prompts to create a bot (name, username)
4. BotFather will provide your **Bot Token** (e.g., `123:ABC...`)
5. Save the token securely

**To find Doctor's Chat ID:**
1. Search for `@userinfobot` on Telegram
2. Send `/start`
3. It will reply with your **Chat ID** (e.g., `7079548109`)
4. Save this ID

### LangSmith (Optional but Recommended)

1. Visit [https://smith.langchain.com/](https://smith.langchain.com/)
2. Sign up with GitHub or email
3. Create new API key in Settings → API Keys
4. Create a new project (e.g., "Healio Clinic")
5. Save **API Key** and **Project Name**

**Benefits:**
- Track all LLM conversations and costs
- Debug graph execution issues
- Monitor performance metrics

### WhatsApp (Twilio or Meta)

**Option A: Twilio (Recommended for MVP)**
1. Sign up at [https://www.twilio.com/whatsapp](https://www.twilio.com/whatsapp)
2. Create a WhatsApp Sandbox (free testing account)
3. Obtain:
   - **Account SID**
   - **Auth Token**
   - **From Number** (sandbox number, e.g., `+1415523...`)
4. Save securely

**Option B: Meta Cloud API (For Production)**
1. Apply at [https://developers.facebook.com/whatsapp](https://developers.facebook.com/whatsapp)
2. Create a Business App
3. Obtain:
   - **Access Token** (from Whatsapp Settings → API Credentials)
   - **Phone Number ID** (from Whatsapp Settings → Phone Numbers)
   - **Verify Token** (create a custom one)
4. Save securely

---

## 2. Environment Configuration

### Create `.env` File

```bash
# Get the template
cp .env.example .env

# Edit with your credentials
nano .env  # Linux/macOS
notepad .env  # Windows
```

### Fill in Required Values

```env
# ── Application ────────────────────────────────────────────────────────────────
APP_ENV=production
LOG_LEVEL=WARNING

# ── OpenAI [REQUIRED] ─────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-proj-YOUR_KEY_HERE
OPENAI_MODEL=gpt-4o-mini
OPENAI_MAX_TOKENS=512
OPENAI_TEMPERATURE=0.2

# ── LangSmith [RECOMMENDED] ──────────────────────────────────────────────────
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_YOUR_KEY_HERE
LANGCHAIN_PROJECT=healio-clinic

# ── Telegram [REQUIRED] ───────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=123:ABC...YOUR_BOT_TOKEN_HERE
DOCTOR_CHAT_ID=7079548109
ALERT_TIMEOUT_SECONDS=30

# ── WhatsApp [OPTIONAL] ────────────────────────────────────────────────────────
# Leave blank to disable WhatsApp initially

# For Twilio (recommended for MVP):
WHATSAPP_PROVIDER=twilio
WHATSAPP_ACCOUNT_SID=ACxxx...
WHATSAPP_AUTH_TOKEN=xxx...
WHATSAPP_FROM_NUMBER=+14155238886

# For Meta Cloud API (production):
# WHATSAPP_PROVIDER=meta
# WHATSAPP_ACCESS_TOKEN=xxx...
# WHATSAPP_PHONE_NUMBER_ID=xxx...
# WHATSAPP_VERIFY_TOKEN=xxx...

# ── Calendar ───────────────────────────────────────────────────────────────────
CALENDAR_PROVIDER=mock
# To enable Google Calendar: see docs/DEPLOYMENT.md
```

### Secure Storage

- [ ] Store `.env` file in secure location (NOT in Git/GitHub)
- [ ] Restrict file permissions: `chmod 600 .env` (Linux/macOS)
- [ ] Use password-protected folders on Windows
- [ ] Consider using environment variable managers:
  - AWS Secrets Manager
  - Azure Key Vault
  - HashiCorp Vault

---

## 3. Infrastructure Setup

### Option A: Local Server Deployment

- [ ] Install Docker: [https://docs.docker.com/get-docker/](https://docs.docker.com/get-docker/)
- [ ] Install Docker Compose: [https://docs.docker.com/compose/install/](https://docs.docker.com/compose/install/)
- [ ] Clone Healio repository: `git clone https://github.com/ramkiGitHub/Healio.git`
- [ ] Navigate to folder: `cd Healio`
- [ ] Start services: `docker-compose up -d`
- [ ] Verify: `curl http://localhost:8000/health`

### Option B: Cloud Deployment

**AWS:**
- [ ] Create AWS account and VPC
- [ ] Push Docker image to ECR (Elastic Container Registry)
- [ ] Set up ECS/Fargate cluster
- [ ] Configure RDS PostgreSQL database
- [ ] Set up Application Load Balancer (ALB)
- [ ] Configure CloudWatch for logs
- [ ] See [DEPLOYMENT.md](./DEPLOYMENT.md) for step-by-step

**Google Cloud:**
- [ ] Create Google Cloud project
- [ ] Push Docker image to Artifact Registry
- [ ] Deploy to Cloud Run
- [ ] Set up Cloud SQL (PostgreSQL)
- [ ] Configure Cloud Load Balancing
- [ ] Set up Cloud Logging
- [ ] See [DEPLOYMENT.md](./DEPLOYMENT.md) for step-by-step

**Azure:**
- [ ] Create Azure subscription and resource group
- [ ] Push Docker image to Container Registry
- [ ] Deploy to Container Instances or App Service
- [ ] Set up Azure Database for PostgreSQL
- [ ] Configure Application Gateway
- [ ] Set up Azure Monitor for logs
- [ ] See [DEPLOYMENT.md](./DEPLOYMENT.md) for step-by-step

### Database Configuration

**For Small Clinics (< 100 patients):**
- [ ] Use SQLite (default, zero configuration)
- [ ] Set `DATABASE_URL=sqlite+aiosqlite:///./data/db/healio.db`
- [ ] Backup daily: `cp ./data/db/healio.db ./backups/`

**For Large Clinics (> 100 patients):**
- [ ] Set up PostgreSQL database
- [ ] Set `DATABASE_URL=postgresql+asyncpg://user:password@host:5432/healio`
- [ ] Configure automated backups in PostgreSQL
- [ ] Test failover procedures

---

## 4. Network & Security

### Firewall Configuration

- [ ] Allow inbound HTTPS (port 443)
- [ ] Allow inbound HTTP (port 80, for redirect to HTTPS)
- [ ] Restrict inbound SSH/RDP (admin access only)
- [ ] Block all other ports

### SSL/TLS Certificate

- [ ] Obtain SSL certificate (free: Let's Encrypt)
- [ ] Install on reverse proxy (Nginx, Apache, or cloud load balancer)
- [ ] Enable automatic renewal (certificate expires every 90 days)
- [ ] Test with: `https://your-domain.com/health`

### Reverse Proxy (Nginx Example)

Create `/etc/nginx/sites-available/healio-ssl`:

```nginx
server {
    listen 443 ssl http2;
    server_name your-clinic-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-clinic-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-clinic-domain.com/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000;
        access_log off;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name your-clinic-domain.com;
    return 301 https://$server_name$request_uri;
}
```

Enable and restart:
```bash
sudo ln -s /etc/nginx/sites-available/healio-ssl /etc/nginx/sites-enabled/
sudo systemctl restart nginx
```

### Webhook Registration

#### Telegram

```bash
# Register webhook URL (one-time)
curl -X POST https://api.telegram.org/bot{YOUR_BOT_TOKEN}/setWebhook \
  -d url=https://your-clinic-domain.com/webhook/telegram

# Verify
curl https://api.telegram.org/bot{YOUR_BOT_TOKEN}/getWebhookInfo
```

#### WhatsApp (Twilio)

1. Go to Twilio Console → Messaging → WhatsApp Sandbox
2. Set webhook URL to: `https://your-clinic-domain.com/webhook/whatsapp`
3. Select "All message and media types"
4. Save settings

#### WhatsApp (Meta)

1. Go to Meta App Dashboard → Whatsapp Business Platform
2. Click "Configuration" → "Webhooks"
3. Set callback URL to: `https://your-clinic-domain.com/webhook/whatsapp`
4. Set verify token (same as `WHATSAPP_VERIFY_TOKEN` in `.env`)
5. Subscribe to message webhook events

---

## 5. Testing & Validation

### Pre-Launch Tests

- [ ] **Health check:** `curl https://your-clinic-domain.com/health`
- [ ] **Send test Telegram message** to your clinic's bot
- [ ] **Verify bot responds** with LLM reply
- [ ] **Check LangSmith traces** at [https://smith.langchain.com/](https://smith.langchain.com/)
- [ ] **Test WhatsApp webhook** (if configured)
- [ ] **Run full test suite:** `pytest tests/ -v`
- [ ] **Verify error logs** contain no critical errors

### Performance Baseline

- [ ] **Measure LLM response time:** Should be 2-10 seconds depending on message length
- [ ] **Verify database performance:** Ensure multi-turn conversations execute smoothly
- [ ] **Test with 5-10 concurrent patients:** Use load testing tool (e.g., k6, Apache JMeter)
- [ ] **Monitor CPU/memory usage:** Should stay <80%

---

## 6. Monitoring & Maintenance

### Daily Checks

- [ ] [ ] Browse logs: `docker logs healio-api` or cloud logging
- [ ] [ ] Check `/health` endpoint responding
- [ ] [ ] Verify Telegram bot is responding to messages
- [ ] [ ] Monitor LangSmith for LLM errors (if tracing enabled)

### Weekly Checks

- [ ] [ ] Review conversation metrics (daily volume, response times)
- [ ] [ ] Backup database (SQLite: copy file; PostgreSQL: automated)
- [ ] [ ] Check SSL certificate expiration: `openssl s_client -connect your-domain.com:443 -showcerts`
- [ ] [ ] Review error logs for patterns

### Monthly Checks

- [ ] [ ] Review clinic staff feedback
- [ ] [ ] Audit OpenAI API costs and usage
- [ ] [ ] Test disaster recovery/failover
- [ ] [ ] Update Docker base image: `docker pull python:3.12-slim`
- [ ] [ ] Review and update security configurations

### Alerts to Configure

- [ ] API returns 500+ errors (health check failing)
- [ ] Response time exceeds 30 seconds (LLM performance degradation)
- [ ] Database disk space > 80% full
- [ ] SSL certificate expires in 14 days
- [ ] OpenAI API quota exceeded or rate limited

---

## 7. Disaster Recovery

### Backup Strategy

**SQLite (Local):**
```bash
# Daily backup script
0 2 * * * cp /path/to/healio/data/db/healio.db /backups/healio-$(date +\%Y\%m\%d).db
```

**PostgreSQL (Cloud):**
- Enable automated backups (AWS: RDS automated backups, Google Cloud: Cloud SQL backups)
- Test restore procedures monthly
- Store backups in separate region

### Recovery Procedures

1. **Application crashes:** Docker auto-restarts (unless resource constraints)
2. **Database corruption:** Restore from latest backup
3. **Complete infrastructure loss:**
   - Deploy new instance
   - Restore database from backup
   - Update DNS to point to new instance
   - Estimated recovery time: 15-30 minutes

---

## 8. HIPAA Compliance (If Required)

### Data Security

- [ ] All data in transit uses HTTPS/TLS
- [ ] All data at rest using encryption (enable EBS encryption, database encryption)
- [ ] API keys stored in encrypted configuration management
- [ ] `.env` file never committed to Git

### Logging & Audit

- [ ] Enable audit logging (CloudTrail, Cloud Audit Logs)
- [ ] Store logs for minimum 7 years
- [ ] Restrict log access to authorized personnel

### Access Control

- [ ] Implement role-based access (RBAC) for admin panel (future feature)
- [ ] Enable MFA for cloud console access
- [ ] Use VPN for admin access to servers

### Data Privacy

- [ ] Disable conversation logging if required (set `LOG_LEVEL=WARNING`)
- [ ] Patient data never sent to external services except:
  - OpenAI (for LLM processing)
  - LangSmith (for optional tracing)
- [ ] Implement data retention policy (auto-delete conversations after 90 days)

---

## 9. Troubleshooting

### Common Issues

**Problem:** "Connection refused" when accessing health endpoint
- **Solution:** Verify Healio service is running (`docker ps`), firewall rules allow port 443

**Problem:** Telegram bot not responding
- **Solution:** Check webhook registered correctly, verify bot token valid, check logs for errors

**Problem:** High OpenAI costs
- **Solution:** Review conversation logs, consider reducing `OPENAI_MAX_TOKENS`, upgrade patient population quality

**Problem:** Slow LLM responses
- **Solution:** Check LangSmith traces for bottlenecks, verify network latency, monitor OpenAI rate limits

### Support Resources

- **Documentation:** [https://github.com/ramkiGitHub/Healio](https://github.com/ramkiGitHub/Healio)
- **Issues:** Create GitHub issue with logs
- **LangSmith Traces:** [https://smith.langchain.com/](https://smith.langchain.com/)
- **OpenAI Status:** [https://status.openai.com/](https://status.openai.com/)

---

## 10. Go-Live Checklist

- [ ] All environment variables configured and tested
- [ ] SSL/TLS certificate installed and verified
- [ ] Webhook URLs registered (Telegram, WhatsApp)
- [ ] Database backup strategy in place
- [ ] Monitoring and alerts configured
- [ ] Staff training completed
- [ ] Disaster recovery plan documented
- [ ] HIPAA compliance (if applicable) verified
- [ ] Load testing completed (≥ 10 concurrent patients)
- [ ] Logs monitored for 24 hours
- [ ] Go-live announcement sent to patients
- [ ] Support team on-call for first week

---

## Post-Launch

- [ ] Collect staff and patient feedback
- [ ] Monitor conversation quality and adjust LLM settings
- [ ] Plan for future features (EHR integration, Google Calendar sync)
- [ ] Schedule quarterly security audits
- [ ] Plan scaling strategy as patient volume grows

---

**Questions?** Deploy with confidence! Reach out with any issues.
