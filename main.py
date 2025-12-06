import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import Base, engine
from logger import logger
from middleware import add_request_id_and_process_time, build_rate_limiter_from_env
from routes import router


Base.metadata.create_all(bind=engine)

app = FastAPI(title="Payments API", version="1.0.0", description="API for handling payments and user authentication")

# Set up CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(add_request_id_and_process_time)
app.middleware("http")(build_rate_limiter_from_env())

app.include_router(router)


@app.get("/")
async def read_root():
    logger.info("Root endpoint accessed")
    return { "status": "success",
        "message": "Welcome to the Payments API!"}
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "message": "Payments API is running", "timestamp": datetime.datetime.utcnow().isoformat()}