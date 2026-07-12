CC ?= gcc
CFLAGS = -Wall -Wextra -O2 -fPIC -shared
LDFLAGS = -ldl
TARGET = libmcptap_fileblock.so
SRC = file_block.c

all: $(TARGET)

$(TARGET): $(SRC)
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f $(TARGET)

install: all
	install -d $(DESTDIR)$(HOME)/.local/lib
	install -m 0755 $(TARGET) $(DESTDIR)$(HOME)/.local/lib/

.PHONY: all clean install
