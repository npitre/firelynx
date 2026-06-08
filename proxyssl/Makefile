# ProxySSL - SSL interception library for Firelynx
# Builds a shared library to intercept SSL calls via LD_PRELOAD

CC = gcc
CFLAGS = -Wall -Wextra -fPIC -shared
LDFLAGS = -lssl -lcrypto -ldl

TARGET = libproxyssl.so
SOURCE = proxyssl.c

.PHONY: all clean test install

all: $(TARGET)

$(TARGET): $(SOURCE)
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f $(TARGET)

test: $(TARGET)
	@echo "Testing ProxySSL library..."
	@echo "Run: LD_PRELOAD=./libproxyssl.so PROXYSSL_DEBUG=1 lynx https://example.com"

install: $(TARGET)
	cp $(TARGET) ../

# Development helpers
debug: CFLAGS += -g -DDEBUG
debug: $(TARGET)

.DEFAULT_GOAL := all