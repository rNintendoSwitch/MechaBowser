FROM gorialis/discord.py:pypi-minimal

WORKDIR /MechaBowser
COPY . /MechaBowser

EXPOSE 8880

RUN pip install -U -r requirements.txt
RUN pip install coverage

CMD ["bash", "coverage.sh"]