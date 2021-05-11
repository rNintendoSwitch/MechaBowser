FROM gorialis/discord.py:master

WORKDIR /MechaBowser
COPY . /MechaBowser

EXPOSE 8880

RUN pip install -U -r requirements.txt

CMD ["python", "app.py"]