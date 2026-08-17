#!/bin/sh
# Deliberately inert: schema convergence is the app's job (app/bootstrap.py,
# run from the FastAPI lifespan), so bare local runs behave identically.
exec "$@"
