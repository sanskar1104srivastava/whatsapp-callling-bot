# build from the repo root:  docker build -f lambdas/dispatcher/Dockerfile .
FROM public.ecr.aws/lambda/python:3.11
WORKDIR /var/task
COPY lambdas/dispatcher/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lambdas/dispatcher/handler.py .
COPY lambdas/common/ .
CMD ["handler.handler"]
