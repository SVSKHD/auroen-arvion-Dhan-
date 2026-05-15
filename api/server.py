from fastapi import FastAPI

from core.watchdog import read_status

app = FastAPI(title="Aureon Arvion Dhan API")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok"
    }


@app.get("/status")
def status() -> dict:
    return read_status()


@app.get("/positions")
def positions() -> dict:
    return {
        "positions": []
    }


@app.get("/paper")
def paper() -> dict:
    return {
        "paper": []
    }
