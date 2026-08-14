#!/bin/bash
# PostgreSQL Setup Script for Codex
# Run this script when PostgreSQL is installed and running

set -e

echo "=========================================="
echo "PostgreSQL Setup for Codex"
echo "=========================================="

# Configuration
DB_NAME="codex_db"
DB_USER="codex_admin"
DB_PASSWORD="securepassword123"
DB_HOST="localhost"
DB_PORT="5432"

echo ""
echo "This script will:"
echo "1. Create the codex_db database"
echo "2. Create the codex_admin user"
echo "3. Grant privileges"
echo "4. Enable pgvector extension"
echo "5. Create all required tables"
echo ""

# Check if PostgreSQL is running
echo "Checking PostgreSQL status..."
if ! pg_isready -q 2>/dev/null; then
    echo "❌ PostgreSQL is not running!"
    echo ""
    echo "Please start PostgreSQL first:"
    echo "  - Ubuntu/Debian: sudo systemctl start postgresql"
    echo "  - macOS: brew services start postgresql"
    echo "  - Windows: net start postgresql"
    echo ""
    exit 1
fi

echo "✅ PostgreSQL is running"

# Create database and user
echo ""
echo "Creating database and user..."

# Check if user exists
if ! psql -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
    echo "Creating user $DB_USER..."
    psql -U postgres -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';"
else
    echo "User $DB_USER already exists"
fi

# Check if database exists
if ! psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
    echo "Creating database $DB_NAME..."
    psql -U postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
else
    echo "Database $DB_NAME already exists"
fi

# Grant privileges
echo "Granting privileges..."
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"
psql -U postgres -d $DB_NAME -c "GRANT ALL ON SCHEMA public TO $DB_USER;"

# Enable pgvector extension
echo "Enabling pgvector extension..."
psql -U postgres -d $DB_NAME -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Run init.sql to create tables
echo ""
echo "Creating database tables..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INIT_SQL="$SCRIPT_DIR/init.sql"

if [ -f "$INIT_SQL" ]; then
    psql -U postgres -d $DB_NAME -f "$INIT_SQL"
    echo "✅ Tables created successfully"
else
    echo "❌ init.sql not found at: $INIT_SQL"
    echo "Please run the init.sql file manually"
fi

# Grant table permissions
echo "Granting table permissions..."
psql -U postgres -d $DB_NAME -c "
DO \$\$
BEGIN
    -- Grant permissions on all tables
    GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO $DB_USER;
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO $DB_USER;
    
    -- Grant permissions on future tables
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO $DB_USER;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO $DB_USER;
END
\$\$;
"

echo ""
echo "=========================================="
echo "✅ PostgreSQL setup complete!"
echo "=========================================="
echo ""
echo "Database: $DB_NAME"
echo "User: $DB_USER"
echo "Host: $DB_HOST"
echo "Port: $DB_PORT"
echo ""
echo "Connection string:"
echo "  postgresql://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"
echo ""
echo "Next steps:"
echo "1. Set DATABASE_URL environment variable:"
echo "   export DATABASE_URL='postgresql://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME'"
echo ""
echo "2. Run the ingestion pipeline:"
echo "   cd /home/mujtaba/new_folder/codex"
echo "   PYTHONPATH=/home/mujtaba/new_folder python3 services/ingestion/ingest.py"
echo ""
echo "3. Start the API server:"
echo "   PYTHONPATH=/home/mujtaba/new_folder python3 -m uvicorn services.api.main:app --host 0.0.0.0 --port 8000"
