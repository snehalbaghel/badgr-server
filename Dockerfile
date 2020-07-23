FROM python:3.7-slim

ARG USER=1001
ARG APPDIR="./badgr"

RUN mkdir $APPDIR
RUN apt-get update && apt-get upgrade -y
RUN apt-get install -y default-libmysqlclient-dev \
                       python3-dev \
                       python3-cairo \
                       build-essential \
                       xmlsec1 \
                       libxmlsec1-dev \
                       pkg-config


LABEL io.k8s.description="S2I builder for Badgr" \
      io.openshift.expose-services="8080:http" \
      io.openshift.tags="badgr,api" \
      io.openshift.s2i.scripts-url="image:///usr/libexec/s2i"

COPY ./.s2i/bin/ /usr/libexec/s2i
RUN chmod 777 -R /usr/libexec/s2i

RUN chown -R 1001:0 ${APPDIR}

EXPOSE 8080


USER 1001

CMD ["/usr/libexec/s2i/run"]
