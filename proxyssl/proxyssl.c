/*
 * ProxySSL - SSL interception library for Firelynx
 *
 * This LD_PRELOAD library intercepts SSL calls from lynx and provides
 * transparent pass-through for connections to our proxy server.
 *
 * When lynx tries to establish SSL to a site through our proxy, we:
 * 1. Return success for the SSL handshake (fake it)
 * 2. Pass through raw HTTP data to/from the proxy
 *
 * This allows lynx to send full HTTPS requests (with paths) as plain HTTP
 * to our proxy, which then processes them with Firefox.
 *
 * For the architecture and the ethics of this local-only SSL interception,
 * see ../docs/how-it-works.md.
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <openssl/ssl.h>
#include <openssl/err.h>
#include <openssl/x509.h>
#include <openssl/evp.h>
#include <openssl/asn1.h>

/* Configuration - configurable via environment variables */
static char proxy_host[256] = "127.0.0.1";
static int proxy_port = 0;  /* Invalid default - must be set via environment variable */

/* Debug logging */
static int debug_enabled = 0;

#define DEBUG(fmt, ...) \
    do { if (debug_enabled) fprintf(stderr, "[ProxySSL] " fmt "\n", ##__VA_ARGS__); } while(0)

/* Original SSL function pointers */
static int (*real_SSL_connect)(SSL *ssl) = NULL;
static int (*real_SSL_read)(SSL *ssl, void *buf, int num) = NULL;
static int (*real_SSL_write)(SSL *ssl, const void *buf, int num) = NULL;
static X509* (*real_SSL_get1_peer_certificate)(const SSL *ssl) = NULL;
static void (*real_SSL_free)(SSL *ssl) = NULL;

/* Structure to track SSL connections that are really proxied */
struct proxy_connection {
    SSL *ssl;
    int socket_fd;
    int is_proxy_connection;
};

/* Simple array to track proxy connections - could use hash table for scale */
#define MAX_CONNECTIONS 64
static struct proxy_connection connections[MAX_CONNECTIONS];
static int num_connections = 0;

/* Initialize the library */
static void __attribute__((constructor)) proxyssl_init(void) {
    char *debug_env = getenv("PROXYSSL_DEBUG");
    debug_enabled = debug_env != NULL && strcmp(debug_env, "1") == 0;

    /* Read proxy configuration from environment */
    char *host_env = getenv("PROXYSSL_HOST");
    if (host_env != NULL) {
        strncpy(proxy_host, host_env, sizeof(proxy_host) - 1);
        proxy_host[sizeof(proxy_host) - 1] = '\0';
    }

    char *port_env = getenv("PROXYSSL_PORT");
    if (port_env != NULL) {
        int port = atoi(port_env);
        if (port > 0 && port < 65536) {
            proxy_port = port;
        }
    }

    DEBUG("ProxySSL initialized: %s:%d, debug=%s", proxy_host, proxy_port, debug_enabled ? "on" : "off");

    /* Load original SSL functions */
    real_SSL_connect = dlsym(RTLD_NEXT, "SSL_connect");
    real_SSL_read = dlsym(RTLD_NEXT, "SSL_read");
    real_SSL_write = dlsym(RTLD_NEXT, "SSL_write");
    real_SSL_get1_peer_certificate = dlsym(RTLD_NEXT, "SSL_get1_peer_certificate");
    real_SSL_free = dlsym(RTLD_NEXT, "SSL_free");

    if (!real_SSL_connect || !real_SSL_read || !real_SSL_write || !real_SSL_get1_peer_certificate || !real_SSL_free) {
        fprintf(stderr, "[ProxySSL] ERROR: Failed to load original SSL functions\n");
    }
}

/* Check if this SSL connection is to our proxy */
static int is_proxy_connection(SSL *ssl) {
    int sock_fd = SSL_get_fd(ssl);
    if (sock_fd < 0) return 0;

    struct sockaddr_in addr;
    socklen_t addr_len = sizeof(addr);

    if (getpeername(sock_fd, (struct sockaddr*)&addr, &addr_len) != 0) {
        return 0;
    }

    /* Check if connected to our proxy */
    if (addr.sin_family == AF_INET &&
        addr.sin_addr.s_addr == inet_addr(proxy_host) &&
        ntohs(addr.sin_port) == proxy_port) {
        DEBUG("Connection to proxy detected (fd=%d)", sock_fd);
        return 1;
    }

    return 0;
}

/* Add connection to tracking list */
static void track_connection(SSL *ssl, int is_proxy) {
    if (num_connections < MAX_CONNECTIONS) {
        connections[num_connections].ssl = ssl;
        connections[num_connections].socket_fd = SSL_get_fd(ssl);
        connections[num_connections].is_proxy_connection = is_proxy;
        num_connections++;
        DEBUG("Tracking connection: ssl=%p, fd=%d, proxy=%s",
              ssl, SSL_get_fd(ssl), is_proxy ? "yes" : "no");
    }
}

/* Find tracked connection */
static struct proxy_connection* find_connection(SSL *ssl) {
    for (int i = 0; i < num_connections; i++) {
        if (connections[i].ssl == ssl) {
            return &connections[i];
        }
    }
    return NULL;
}

/* Remove connection from tracking list */
static void untrack_connection(SSL *ssl) {
    for (int i = 0; i < num_connections; i++) {
        if (connections[i].ssl == ssl) {
            DEBUG("Untracking connection: ssl=%p, fd=%d", ssl, connections[i].socket_fd);

            /* Move last connection to this slot to avoid holes */
            if (i < num_connections - 1) {
                connections[i] = connections[num_connections - 1];
            }
            num_connections--;
            return;
        }
    }
}

/* Intercepted SSL_connect */
int SSL_connect(SSL *ssl) {
    if (!real_SSL_connect) {
        DEBUG("ERROR: real_SSL_connect not loaded");
        return -1;
    }

    int is_proxy = is_proxy_connection(ssl);
    track_connection(ssl, is_proxy);

    if (is_proxy) {
        DEBUG("Faking SSL_connect success for proxy connection");
        return 1; /* Success - fake it! */
    } else {
        DEBUG("Calling real SSL_connect for non-proxy connection");
        return real_SSL_connect(ssl);
    }
}

/* Intercepted SSL_read */
int SSL_read(SSL *ssl, void *buf, int num) {
    struct proxy_connection *conn = find_connection(ssl);

    if (conn && conn->is_proxy_connection) {
        /* For proxy connections, read raw data from socket */
        DEBUG("Reading raw data from proxy connection (fd=%d)", conn->socket_fd);
        return recv(conn->socket_fd, buf, num, 0);
    } else {
        /* For real SSL connections, use real SSL_read */
        return real_SSL_read ? real_SSL_read(ssl, buf, num) : -1;
    }
}

/* Intercepted SSL_write */
int SSL_write(SSL *ssl, const void *buf, int num) {
    struct proxy_connection *conn = find_connection(ssl);

    if (conn && conn->is_proxy_connection) {
        /* For proxy connections, write raw data to socket */
        DEBUG("Writing raw data to proxy connection (fd=%d), %d bytes", conn->socket_fd, num);
        if (debug_enabled && num > 0) {
            /* Log first line of HTTP request for debugging */
            char *newline = memchr(buf, '\n', num);
            int first_line_len = newline ? (newline - (char*)buf) : (num > 100 ? 100 : num);
            DEBUG("HTTP request: %.*s", first_line_len, (char*)buf);
        }
        return send(conn->socket_fd, buf, num, 0);
    } else {
        /* For real SSL connections, use real SSL_write */
        return real_SSL_write ? real_SSL_write(ssl, buf, num) : -1;
    }
}

/* Create a dummy certificate for proxy connections with matching hostname */
static X509* create_dummy_certificate(SSL *ssl) {
    X509 *cert = X509_new();
    if (!cert) return NULL;

    /* Set a dummy serial number */
    ASN1_INTEGER_set(X509_get_serialNumber(cert), 1);

    /* Set validity period (1 year from now) */
    X509_gmtime_adj(X509_get_notBefore(cert), 0);
    X509_gmtime_adj(X509_get_notAfter(cert), 365*24*60*60);

    /* Extract hostname from SSL connection */
    const char *hostname = SSL_get_servername(ssl, TLSEXT_NAMETYPE_host_name);
    if (!hostname) {
        /* Fallback - try to get hostname from socket peer address */
        int sock_fd = SSL_get_fd(ssl);
        struct sockaddr_in addr;
        socklen_t addr_len = sizeof(addr);
        if (getpeername(sock_fd, (struct sockaddr*)&addr, &addr_len) == 0) {
            /* For proxy connections, we know it's to our proxy, so use a generic name */
            hostname = "proxy.local";
        } else {
            hostname = "localhost";
        }
    }

    /* Create subject name - clearly indicating this is a proxy certificate */
    X509_NAME *subject = X509_NAME_new();
    X509_NAME *issuer = X509_NAME_new();
    if (subject && issuer) {
        X509_NAME_add_entry_by_txt(subject, "CN", MBSTRING_ASC,
                                   (unsigned char*)hostname, -1, -1, 0);
        X509_NAME_add_entry_by_txt(issuer, "CN", MBSTRING_ASC,
                                   (unsigned char*)"Firelynx Local Proxy", -1, -1, 0);
        X509_NAME_add_entry_by_txt(issuer, "O", MBSTRING_ASC,
                                   (unsigned char*)"Firelynx Accessibility Proxy", -1, -1, 0);
        X509_set_subject_name(cert, subject);
        X509_set_issuer_name(cert, issuer);
        X509_NAME_free(subject);
        X509_NAME_free(issuer);
    }

    /* Generate a dummy 2048-bit RSA key (OpenSSL 3.x EVP API) */
    EVP_PKEY *pkey = EVP_RSA_gen(2048);
    if (pkey) {
        X509_set_pubkey(cert, pkey);
        /* Self-sign the certificate */
        X509_sign(cert, pkey, EVP_sha256());
        EVP_PKEY_free(pkey);
    }

    DEBUG("Created dummy certificate for proxy connection with CN=%s", hostname);
    return cert;
}

/* Intercepted SSL_get1_peer_certificate */
X509* SSL_get1_peer_certificate(const SSL *ssl) {
    struct proxy_connection *conn = find_connection((SSL*)ssl);

    if (conn && conn->is_proxy_connection) {
        /* For proxy connections, return a dummy certificate */
        DEBUG("Returning dummy certificate for proxy connection");
        return create_dummy_certificate((SSL*)ssl);
    } else {
        /* For real SSL connections, use real function */
        return real_SSL_get1_peer_certificate ? real_SSL_get1_peer_certificate(ssl) : NULL;
    }
}

/* Intercepted SSL_free */
void SSL_free(SSL *ssl) {
    if (!real_SSL_free) {
        DEBUG("ERROR: real_SSL_free not loaded");
        return;
    }

    struct proxy_connection *conn = find_connection(ssl);
    int is_proxy = (conn && conn->is_proxy_connection);

    if (conn) {
        untrack_connection(ssl);
    }

    if (is_proxy) {
        DEBUG("Cleaning up faked proxy connection");
    } else {
        /* Real SSL connection or untracked connection: call real SSL_free */
        real_SSL_free(ssl);
    }
}
