FROM zenika/alpine-chrome:124

# Install nginx with perl module to act as a proxy for host header mapping
USER root
RUN apk add --no-cache nginx nginx-mod-http-perl

# Replace the default nginx configuration with our proxy config
COPY nginx.conf /etc/nginx/nginx.conf

# Create temporary directory for Chrome user data
RUN mkdir -p /tmp/chrome-dev-session && \
  chmod 777 /tmp/chrome-dev-session

# Create Chrome policy directories to eliminate policy warnings
RUN mkdir -p /etc/chromium/policies/managed /etc/chromium/policies/recommended && \
  chmod 755 /etc/chromium/policies/managed /etc/chromium/policies/recommended

# Create D-Bus directory and fake socket to prevent connection errors
RUN mkdir -p /var/run/dbus && \
  touch /var/run/dbus/system_bus_socket && \
  chmod 666 /var/run/dbus/system_bus_socket

# Set environment variables to reduce warnings
ENV CHROME_LOG_FILE=/tmp/chrome.log
ENV CHROME_DEVEL_SANDBOX=/dev/null

# Create nginx directories and set permissions
RUN mkdir -p /run/nginx /var/log/nginx && \
  chmod 755 /run/nginx

# Copy the entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Expose both Chrome and proxy ports
EXPOSE 8080

# Use the entrypoint script
ENTRYPOINT ["/entrypoint.sh"]