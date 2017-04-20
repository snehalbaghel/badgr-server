import uuid

import basic_models
import cachemodel
import os
from autoslug import AutoSlugField
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.urlresolvers import reverse
from django.db import models
from jsonfield import JSONField

from composition.sharing import SharingManager
from issuer.models import BadgeInstance, BadgeClass
from mainsite.utils import OriginSetting

AUTH_USER_MODEL = getattr(settings, 'AUTH_USER_MODEL', 'auth.User')


class LocalBadgeInstance(cachemodel.CacheModel):
    issuer_badgeclass = models.ForeignKey("issuer.BadgeClass", blank=True, null=True)

    recipient_user = models.ForeignKey(AUTH_USER_MODEL)

    # migrated from AbstractComponent
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(AUTH_USER_MODEL, blank=True, null=True, related_name="+")
    identifier = models.CharField(max_length=1024, null=False, default='get_full_url')
    json = JSONField()

    # migrated from AbstractBadgeInstance
    recipient_identifier = models.EmailField(max_length=1024, blank=False, null=False)
    image = models.FileField(upload_to='uploads/badges', blank=True)
    slug = AutoSlugField(max_length=255, populate_from='populate_slug', unique=True, blank=False, editable=False)
    revoked = models.BooleanField(default=False)
    revocation_reason = models.CharField(max_length=255, blank=True, null=True, default=None)

    def get_absolute_url(self):
        return reverse('badgeinstance_json', kwargs={'slug': self.slug})

    def get_public_url(self):
        return OriginSetting.HTTP+self.get_absolute_url()

    def populate_slug(self):
        return str(uuid.uuid4())

    def image_url(self):
        if getattr(settings, 'MEDIA_URL').startswith('http'):
            return default_storage.url(self.image.name)
        else:
            return getattr(settings, 'HTTP_ORIGIN') + default_storage.url(self.image.name)

    def publish(self):
        super(LocalBadgeInstance, self).publish()
        self.publish_by('slug')
        self.publish_by('slug', 'revoked')

    def delete(self, *args, **kwargs):
        super(LocalBadgeInstance, self).delete(*args, **kwargs)
        self.publish_delete('slug')
        self.publish_delete('slug', 'revoked')

    @property
    def share_url(self):
        return OriginSetting.HTTP+reverse('shared_badge', kwargs={'badge_id': self.pk})

    @property
    def cached_issuer(self):
        return self.cached_badgeclass.cached_issuer

    @property
    def cached_badgeclass(self):
        return BadgeClass.cached.get(pk=self.issuer_badgeclass_id)

    @property
    def acceptance(self):
        return 'Accepted'

    @property
    def owner(self):
        return self.recipient_user

    def get_full_url(self):
        try:
            return self.json['id']
        except (KeyError, TypeError):
            if self.get_absolute_url().startswith('/'):
                return OriginSetting.JSON + self.get_absolute_url()
            else:
                return '_:null'

    @property
    def jsonld_id(self):
        return self.get_full_url()


class Collection(cachemodel.CacheModel):
    name = models.CharField(max_length=128)
    slug = AutoSlugField(max_length=128, populate_from='name', blank=False,
                         editable=True)
    description = models.CharField(max_length=255, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=False)
    share_hash = models.CharField(max_length=255, null=False, blank=True)

    instances = models.ManyToManyField(LocalBadgeInstance,
                                       through='LocalBadgeInstanceCollection')
    shared_with = models.ManyToManyField(AUTH_USER_MODEL,
                                         through='CollectionPermission',
                                         related_name='shared_with_me')

    class Meta:
        unique_together = ('owner', 'slug')

    # Convenience methods for toggling published state
    @property
    def published(self):
        return bool(self.share_hash)

    @published.setter
    def published(self, value):
        if value and not self.share_hash:
            self.share_hash = os.urandom(16).encode('hex')
        elif not value and self.share_hash:
            self.share_hash = ''

    @property
    def share_url(self):
        if self.share_hash != '':
            return getattr(settings, 'HTTP_ORIGIN') + reverse(
                'shared_collection', kwargs={'share_hash': self.share_hash})
        return ''


class LocalBadgeInstanceCollectionManager(models.Manager):
    def find(self, recipient_user, collection_slug, badge_id, queryset=None):
        if queryset:
            base_queryset = queryset
        else:
            base_queryset = self.all()

        try:
            return base_queryset.get(
                instance__recipient_user=recipient_user,
                collection__slug=collection_slug,
                instance__id=int(badge_id)
            )
        except LocalBadgeInstanceCollection.DoesNotExist:
            pass
        except ValueError:
            # when badge_id is not an int, it's a issuer_instance slug
            try:
                item = base_queryset.get(
                    collection__slug=collection_slug,
                    issuer_instance__slug=badge_id
                )
                assert(item.badge_instance.recipient_identifier
                       in recipient_user.all_recipient_identifiers)
                return item
            except (LocalBadgeInstanceCollection.DoesNotExist, AssertionError,):
                pass

    def find_many(self, recipient_user, collection_slug):
        collection = Collection.objects.get(owner=recipient_user, slug=collection_slug)
        return collection.badges.all()


class LocalBadgeInstanceCollection(models.Model):
    instance = models.ForeignKey(LocalBadgeInstance, null=True)
    issuer_instance = models.ForeignKey("issuer.BadgeInstance", null=True)
    collection = models.ForeignKey(Collection, null=False, related_name='badges')

    description = models.TextField(blank=True)

    objects = LocalBadgeInstanceCollectionManager()

    class Meta:
        unique_together = ('instance', 'issuer_instance', 'collection')
        verbose_name = "BadgeInstance in a Collection"
        verbose_name_plural = "BadgeInstances in Collections"

    def __unicode__(self):
        return u'{} in {}\'s {}'.format(
            self.badge_instance.issuer_badgeclass.name,
            self.badge_instance.recipient_identifier,
            self.collection.name
        )

    @property
    def badge_instance(self):
        if self.instance_id:
            return self.instance
        elif self.issuer_instance_id:
            return self.issuer_instance

    @property
    def badge_id(self):
        if self.instance_id:
            return self.instance_id
        elif self.issuer_instance_id:
            return self.issuer_instance.slug


class CollectionPermission(models.Model):
    user = models.ForeignKey(AUTH_USER_MODEL, null=False)
    collection = models.ForeignKey(Collection, null=False)

    can_write = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'collection')


class BaseSharedModel(cachemodel.CacheModel, basic_models.TimestampedModel):
    SHARE_PROVIDERS = [(p.provider_code, p.provider_name) for code,p in SharingManager.ManagerProviders.items()]
    provider = models.CharField(max_length=254, choices=SHARE_PROVIDERS)

    class Meta:
        abstract = True

    def get_share_url(self, provider, **kwargs):
        raise NotImplementedError()


class LocalBadgeInstanceShare(BaseSharedModel):
    instance = models.ForeignKey("composition.LocalBadgeInstance", null=True)
    issuer_instance = models.ForeignKey("issuer.BadgeInstance", null=True)

    def set_badge(self, badge):
        if isinstance(badge, LocalBadgeInstance):
            self.instance = badge
        elif isinstance(badge, BadgeInstance):
            self.issuer_instance = badge
        else:
            raise ValueError("unknown badge type")

    @property
    def badge(self):
        if self.instance_id:
            return self.instance
        elif self.issuer_instance_id:
            return self.issuer_instance

    def get_share_url(self, provider, **kwargs):
        return SharingManager.share_url(provider, self.badge, **kwargs)


class CollectionShare(BaseSharedModel):
    collection = models.ForeignKey(Collection, null=False)

    def get_share_url(self, provider, **kwargs):
        return SharingManager.share_url(provider, self.collection, **kwargs)
