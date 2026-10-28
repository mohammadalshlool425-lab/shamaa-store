"""Deployment/compatibility entrypoint. Isolated tests import application instead."""
from .application import create_app

app = create_app()


def main():
    import os
    import uvicorn
    uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','8000')),access_log=False)


if __name__=='__main__':
    main()
