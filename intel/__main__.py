import argparse
import uvicorn
from .app import create_app


def main():
    parser = argparse.ArgumentParser(description='AI INFO 情报作战台')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False)


if __name__ == '__main__':
    main()
