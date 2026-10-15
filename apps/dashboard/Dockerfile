FROM node:20-alpine

WORKDIR /app

# Install pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

# Copy package files
COPY apps/dashboard/package.json apps/dashboard/pnpm-lock.yaml* ./

# Install dependencies
RUN pnpm install --frozen-lockfile

# Copy application code
COPY apps/dashboard .

# Build (for production)
# RUN pnpm build

# Expose port
EXPOSE 3000

# Run development server
CMD ["pnpm", "dev"]