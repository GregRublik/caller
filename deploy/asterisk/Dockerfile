FROM alpine:3.21

RUN apk add --no-cache asterisk

# Only these four files are replaced; the rest of the package config stays.
COPY config/pjsip.conf /etc/asterisk/pjsip.conf
COPY config/extensions.conf /etc/asterisk/extensions.conf
COPY config/modules.conf /etc/asterisk/modules.conf
COPY config/rtp.conf /etc/asterisk/rtp.conf

EXPOSE 5062/udp 5062/tcp 21000-21100/udp

CMD ["asterisk", "-f", "-vvv"]
