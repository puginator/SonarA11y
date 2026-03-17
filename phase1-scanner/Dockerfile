FROM mcr.microsoft.com/playwright:v1.58.2-noble

WORKDIR /app
COPY phase1-scanner/package.json ./
RUN npm install

COPY phase1-scanner/src ./src
COPY contracts /contracts

EXPOSE 4001
CMD ["npm", "start"]
