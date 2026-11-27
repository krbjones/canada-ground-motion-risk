FROM node:22-alpine
WORKDIR /app
# Static dashboard: the precomputed national grid, stacks and calibration live in public/data.
# The Python pipeline (scripts/) runs locally, not in the image.
COPY package.json server.js ./
COPY public ./public
ENV HOST=0.0.0.0 PORT=8090 NODE_ENV=production
EXPOSE 8090
USER node
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:${PORT}/healthz || exit 1
CMD ["node", "server.js"]
