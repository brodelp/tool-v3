# Extra trusted root CAs

Put PEM root certificates here as `*.crt` files, for example an enterprise root that signs an
internal LLM gateway. `docker compose up --build` appends them to certifi's bundle in the runtime
image and points `SSL_CERT_FILE` at the result; without any, trust is unchanged. A file that is not
PEM fails the build. Files here other than this README are git-ignored; `git add -f` one to share
it.
