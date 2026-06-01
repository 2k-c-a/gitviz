from fastapi import FastAPI
from app.core.database import Base, engine

from app.models import repository
from app.models import analysis_job

from app.api.repositories import router as repository_router

app = FastAPI()

Base.metadata.create_all(bind=engine)

app.include_router(repository_router)


@app.get("/")
def root():
    return {"message": "GitViz API Running"}