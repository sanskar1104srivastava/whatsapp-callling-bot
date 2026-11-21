# build from the repo root:  docker build -f lambdas/webhook_receiver/Dockerfile .
FROM public.ecr.aws/lambda/python:3.11
WORKDIR /var/task
COPY lambdas/webhook_receiver/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lambdas/webhook_receiver/handler.py .
COPY lambdas/common/ .
CMD ["handler.handler"]
