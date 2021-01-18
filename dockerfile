FROM gorialis/discord.py:master

WORKDIR /app
ADD ./MechaBowser /app

EXPOSE 8880

RUN pip install -r requirements.txt

CMD ["python", "app.py"]