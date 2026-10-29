import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from services.core.config import Settings
from services.core.db import Database
from services.sim_g3 import mount


def create_app():
    Settings.load()
    db = Database(os.environ["SIM_B_DATABASE_URL"], max_size=4)

    @asynccontextmanager
    async def lifespan(app):
        db.pool.open()
        yield
        db.pool.close()

    app = FastAPI(title="SIM-B independent channel", lifespan=lifespan)
    mount(app, db, "SIM-B")
    return app
