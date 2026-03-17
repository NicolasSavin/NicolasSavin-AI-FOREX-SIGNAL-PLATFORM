try:
    from fastapi import FastAPI
except ImportError:
    raise ImportError("FastAPI is not installed. Please run 'pip install fastapi' to install it.")

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Forex Signal Platform 3.0"}