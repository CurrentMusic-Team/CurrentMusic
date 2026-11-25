# Self-hosted verification mail

This Debian Exim service submits verification mail on `127.0.0.1:1587` using
authenticated STARTTLS. It uses its own spool and sends to recipients' MX
servers over TCP 25. Use a separate transport for bulk notifications.

1. Verify that the hosting provider permits outbound TCP 25. Choose a DNS-only
   hostname for mail, such as `mail.example.com`. Point its A record and the
   sending IPv4 address's PTR record at one another. Publish an SPF TXT record on
   the hostname authorizing the IP, a DMARC TXT record at
   `_dmarc.mail.example.com`, and the DKIM public key at
   `currentmusic._domainkey.mail.example.com`. Start DMARC with `p=none`
   while testing delivery.
2. Install Debian's Exim package. Copy `exim.conf.example` to
   `/etc/currentmusic-mail/exim.conf` and change `MAIL_DOMAIN` to the hostname
   above. Place a TLS server certificate with a `localhost` SAN and its key at
   `localhost.crt` and `localhost.key`. Place a 2048-bit RSA DKIM key at
   `dkim.key`. The Exim service needs read access to both private keys.
3. Create `/etc/currentmusic-mail/passwd` with a line
   `currentmusic:HASH`, using a SHA-512 crypt hash from `openssl passwd -6`.
   Keep the password file and private keys readable only by root and the
   Exim service group. Install `currentmusic-mail.service` in
   `/etc/systemd/system/`, reload systemd and start the unit.
4. Configure the backend's protected environment file with
   `CM_SMTP_HOST=localhost`, `CM_SMTP_PORT=1587`,
   `CM_SMTP_SECURITY=starttls`, `CM_SMTP_USER=currentmusic`,
   `CM_SMTP_FROM=verify@mail.example.com`, and `CM_SMTP_PASS` set to the
   password corresponding to the hash. If using a private certificate, set
   `CM_SMTP_CA_FILE` to its public PEM or its issuer's PEM.
5. Before switching production, send to a controlled inbox and check the
   receiving provider's authentication results and the Exim delivery log.
   SMTP acceptance by local Exim indicates that the message entered the
   queue. The receiving provider's reply confirms external handoff.
