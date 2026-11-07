FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy
WORKDIR /app
COPY filler.py profile.example.json ./
# Nothing persists: no volumes, no profile dir. The run directory is the
# only thing mounted in, and it holds evidence + the APPROVE gate file.
ENTRYPOINT ["python", "filler.py"]
