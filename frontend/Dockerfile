# Imagem de produção do frontend (Next.js standalone), em 3 estágios: só o último vai
# para o servidor, sem código-fonte, sem node_modules completo, sem ferramentas de build.

FROM node:22-alpine AS dependencias
WORKDIR /app
COPY package.json package-lock.json ./
# npm ci: instala exatamente o package-lock.json
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencias /app/node_modules ./node_modules
COPY . .
# public/ pode não existir no git (pasta vazia não é versionada)
RUN mkdir -p public && npm run build

FROM node:22-alpine AS execucao
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# O usuário "node" já existe na imagem oficial e não tem privilégios
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
# BACKEND_URL e BFF_SEGREDO chegam em tempo de execução (compose), nunca na imagem
CMD ["node", "server.js"]
