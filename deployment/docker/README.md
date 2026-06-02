# Face Recognition Attendance System - Docker Deployment

This guide explains how to deploy the Face Recognition Attendance System using Docker with pgAdmin as the database management tool.

## Directory Structure

- `app/` - Contains the Dockerfile for the main Flask application
- `webrtc/` - Contains the Dockerfile for the WebRTC server
- `nginx/` - Contains Nginx configuration files
  - `conf.d/` - Nginx configuration files
  - `ssl/` - SSL certificates (for HTTPS in production)
- `pgadmin/` - pgAdmin configuration files
  - `servers.json` - Automatic server configuration for pgAdmin

## Quick Start

1. Make sure you have Docker and Docker Compose installed on your system.
2. The `.env` file is already configured with default values. You can modify it if needed:
   ```
   DB_USER=postgres
   DB_PASS=bittu123
   DB_NAME=attendance_db
   PGADMIN_EMAIL=admin@example.com
   PGADMIN_PASSWORD=admin123
   ```
3. Start the services:
   ```
   docker-compose up -d
   ```
4. Access the application at `http://localhost`
5. Access pgAdmin at `http://localhost/pgadmin/`
   - Login with the email and password specified in the .env file (default: admin@example.com / admin123)
   - The PostgreSQL server is pre-configured and should appear in the server list
   - If prompted for a password, use the DB_PASS value from your .env file (default: bittu123)

## Services

- **web**: The main Flask application
- **webrtc**: The WebRTC server for real-time video processing
- **db**: PostgreSQL database
- **pgadmin**: Web-based PostgreSQL administration tool
- **nginx**: Web server for routing and SSL termination

## Database Management with pgAdmin

pgAdmin is included in this deployment for easy database management:

1. Access pgAdmin at http://localhost/pgadmin/
2. Log in with the credentials from your .env file
3. The PostgreSQL server should be automatically configured
4. You can manage the database, run queries, and perform backups directly from pgAdmin

## Backup and Restore

### Creating a Database Backup

1. Log in to pgAdmin
2. Right-click on the database (attendance_db)
3. Select "Backup..."
4. Configure the backup settings and click "Backup"

### Restoring a Database

1. Log in to pgAdmin
2. Right-click on the database (attendance_db)
3. Select "Restore..."
4. Configure the restore settings and click "Restore"

## Troubleshooting

If any container fails to start, check the logs:

```bash
docker-compose logs web     # For the web application
docker-compose logs webrtc  # For the WebRTC server
docker-compose logs db      # For the PostgreSQL database
docker-compose logs pgadmin # For pgAdmin
```

## Security Considerations for Production

For production deployments, consider:

1. Changing all default passwords in the .env file
2. Enabling HTTPS by uncommenting the HTTPS section in the nginx configuration
3. Generating SSL certificates:
   ```
   mkdir -p nginx/ssl
   openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout nginx/ssl/key.pem -out nginx/ssl/cert.pem
   ```
4. Regularly backing up your database

## Stopping the System

To stop all containers:

```bash
docker-compose down
```

To stop and remove all data (including the database):

```bash
docker-compose down -v
```
