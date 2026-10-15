-- Create test database
SELECT 'CREATE DATABASE socialerp_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'socialerp_test')\gexec

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE socialerp_test TO socialerp;