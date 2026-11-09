# EduTrack deployment checklist

## Recommended deployment shape
Deploy two Docker web services and a managed MySQL database:

1. **Backend service**: repository root as build context, Dockerfile `backend/Dockerfile`, listen on port `8000`.
2. **Frontend service**: repository root as build context, Dockerfile `frontend/Dockerfile`, listen on port `80`. Set `BACKEND_URL` to the backend's public HTTPS URL (no trailing slash). Nginx proxies `/api/*` to FastAPI, so browser requests and the login cookie remain same-origin from the frontend domain.
3. **Database**: managed MySQL 8.0+ / 8.4. Set its host, port, database, user and password on the backend service.

## Backend environment variables
Set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `SECRET_KEY`, `COOKIE_SECURE=true`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ATTENDANCE_THRESHOLD=75`, and `LOW_MARKS_THRESHOLD=40`. Configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM` for automatic email delivery. Keep all secrets in the hosting provider's environment settings, never in GitHub source files.

## Frontend environment variables
Set `BACKEND_URL=https://<your-backend-host>` on the frontend Docker service. The frontend image uses the official Nginx entrypoint to substitute this value into its reverse-proxy configuration at container startup.

## GitHub Actions deployment
The workflow builds the React bundle, checks Python syntax and unit tests, and builds both Docker images on pushes and pull requests. For automatic Render deployment after a successful push to `main`, create backend and frontend Docker web services and add repository secrets `RENDER_BACKEND_DEPLOY_HOOK` and `RENDER_FRONTEND_DEPLOY_HOOK`. Configure the service environment variables above before triggering deployment.

## Important
Use HTTPS for the public frontend and backend. Set `COOKIE_SECURE=true` on the backend in production. Make sure the backend can reach the managed MySQL database. Free hosting plans may sleep or have resource limitations; verify availability and email delivery before the expo.
