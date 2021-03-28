FROM gorialis/discord.py:master

WORKDIR /MechaBowser
COPY . /MechaBowser

EXPOSE 8880

RUN pip install -r requirements.txt

CMD ["python", "app.py"]