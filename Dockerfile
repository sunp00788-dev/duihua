FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY server.py llm.py image.py store.py mindmap.py prompts.py router.py memory.py ./
COPY static ./static

ENV HOST=0.0.0.0
ENV PORT=8765
ENV PYTHONUNBUFFERED=1

EXPOSE 8765

CMD ["python", "server.py"]
