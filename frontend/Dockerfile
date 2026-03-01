# simple development Dockerfile for the React frontend
FROM node:18-alpine
WORKDIR /app

# install dependencies once. volume mount will override node_modules
COPY package*.json ./
RUN npm ci

# copy the rest of the source tree; compose mounts the live code over /app
COPY . .

CMD ["npm", "run", "dev"]
