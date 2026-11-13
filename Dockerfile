# AI CFO: hosted live demo (FastAPI + the Node signer used for NEAR anchoring)
FROM node:22-slim AS near
WORKDIR /near
COPY near/package.json near/package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim
COPY --from=near /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=near /near/node_modules near/node_modules
RUN mkdir -p data
ENV PUBLIC_DEMO=true PYTHONUNBUFFERED=1
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
