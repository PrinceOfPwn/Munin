# Training-only image. Apache 2.4.49 is intentionally pinned so version
# detection can exercise a known-CVE fingerprint in an isolated local lab.
FROM httpd:2.4.49

COPY index.html /usr/local/apache2/htdocs/index.html
RUN printf '\n# Munin local training fixture only\nServerTokens Full\nServerSignature On\n' >> /usr/local/apache2/conf/httpd.conf
