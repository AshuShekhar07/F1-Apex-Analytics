from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routers import races, drivers, tracks, practice, predictions

app = FastAPI(
    title="Apex21 API",
    description="F1 analytics and prediction platform backend",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # Vite/React dev servers
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(races.router)
app.include_router(drivers.router)
app.include_router(tracks.router)
app.include_router(practice.router)
app.include_router(predictions.router)


@app.get("/")
def root():
    return {"status": "Apex21 API is running"}
