from django.contrib.gis.db import models


class Zona(models.Model):
    nama = models.CharField(max_length=100)
    geom = models.MultiPolygonField(srid=4326)
    dibuat = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'zona'

    def __str__(self):
        return self.nama
