from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routers import races, drivers, tracks, practice, predictions, compare, wet_weather, predict_and_compete, records, strategy, teams

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
app.include_router(compare.router)
app.include_router(wet_weather.router)
app.include_router(predict_and_compete.router)
app.include_router(records.router)
app.include_router(strategy.router)
app.include_router(teams.router)


@app.get("/")
def root():
    return {"status": "Apex21 API is running"}
