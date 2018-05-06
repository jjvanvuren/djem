from collections import OrderedDict
from functools import wraps

from django.conf import settings
from django.contrib.auth.mixins import PermissionRequiredMixin as DjangoPermissionRequiredMixin
from django.contrib.auth.models import Permission, _user_has_perm
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, resolve_url
from django.utils import six
from django.utils.decorators import available_attrs
from django.utils.six.moves.urllib.parse import urlparse

DEFAULT_403 = getattr(settings, 'DJEM_DEFAULT_403', False)


class OLPMixin(object):
    """
    A mixin for a custom user model that enables additional advanced features
    of the object-level permission system.
    """
    
    def __init__(self, *args, **kwargs):
        
        self._olp_cache = {}
        
        self._active_logs = OrderedDict()
        self._finished_logs = OrderedDict()
        
        super(OLPMixin, self).__init__(*args, **kwargs)
    
    def has_perm(self, perm, obj=None):
        
        # Use the default behaviour unless DJEM_UNIVERSAL_OLP is True
        if not getattr(settings, 'DJEM_UNIVERSAL_OLP', False):
            return super(OLPMixin, self).has_perm(perm, obj)
        
        # Customise behaviour - active superusers implicitly have all
        # model-level permissions, but are subject to object-level checks
        if not obj and self.is_active and self.is_superuser:
            return True
        
        return _user_has_perm(self, perm, obj)
    
    def clear_perm_cache(self):
        """
        Clear the object-level and model-level permissions caches on the user
        instance.
        """
        
        # Clear the object-level permissions cache
        self._olp_cache = {}
        
        # Clear the model-level permissions cache as well, for good measure
        del self._user_perm_cache
        del self._group_perm_cache
        del self._perm_cache
    
    def start_log(self, name):
        """
        Start a new log with the given ``name``. The new log becomes the
        current "active" log.
        
        :param name: The name of the log.
        """
        
        if name in self._active_logs:
            raise ValueError('A log named "{0}" is already active.'.format(name))
        
        self._active_logs[name] = []
    
    def end_log(self):
        """
        End the currently active log. A log must be ended in order to be
        retrieved.
        """
        
        # Move from active logs to finished logs
        name, log = self._active_logs.popitem()
        
        # If a log with the same name has been finished previously, remove it
        # from the finished logs dict before adding this one, so that this one
        # is added to the "end" of the ordered dict.
        if name in self._finished_logs:
            self._finished_logs.pop(name)
        
        self._finished_logs[name] = log
    
    def log(self, *lines):
        """
        Append to the currently active log. Each given argument will be added
        as a separate line to the log.
        
        :param lines: Individual lines to add to the log.
        """
        
        # Pop to get the last item
        name, log = self._active_logs.popitem()
        
        # Append to the log
        log.extend(lines)
        
        # Put back in the active logs dict
        self._active_logs[name] = log
    
    def get_log(self, name, raw=False):
        """
        Return the named log, as a string. The log must have been ended (via
        ``end_log()``) in order to retrieve it. Can return the raw list of lines
        in the log if ``raw=True``.
        
        :param name: The name of the log to retrieve.
        :param raw: ``True`` to return the log as a list. Returned as a string by default.
        :return: The log, either as a string or a list.
        """
        
        try:
            log = self._finished_logs[name]
        except KeyError:
            raise KeyError('No log found for "{0}". Has it been finished?'.format(name))
        
        if raw:
            return log
        
        return '\n'.join(log)
    
    def get_last_log(self, raw=False):
        """
        Return the most recently finished log, as a string. Can return the raw
        list of lines in the log if ``raw=True``.
        
        :param raw: ``True`` to return the log as a list. Returned as a string by default.
        :return: The log, either as a string or a list.
        """
        
        # Pop to get the last item, but put it straight back
        try:
            name, log = self._finished_logs.popitem()
        except KeyError:
            raise KeyError('No finished logs to retrieve.')
        
        self._finished_logs[name] = log
        
        if raw:
            return log
        
        return '\n'.join(log)


class ObjectPermissionsBackend(object):
    
    def authenticate(self, *args, **kwargs):
        
        return None
    
    def _get_object_permission(self, perm, user_obj, obj, from_name):
        """
        Test if a user has a permission on a specific model object.
        ``from_name`` can be either "user" or "group", to determine permissions
        using the user object itself or the groups it belongs to, respectively.
        """
        
        if not user_obj.is_active:
            return False
        
        try:
            perm_cache = user_obj._olp_cache
        except AttributeError:
            # OLP cache dictionary will not exist by default if not using
            # OLPMixin and no permissions have yet been checked on this user
            # object
            perm_cache = user_obj._olp_cache = {}
        
        perm_cache_name = '{0}-{1}-{2}'.format(from_name, perm, obj.pk)
        
        if perm_cache_name not in perm_cache:
            if not user_obj.has_perm(perm):
                # The User needs to have model-level permissions in order to
                # get object-level permissions
                access = False
            else:
                access_fn_name = '_{0}_can_{1}'.format(
                    from_name,
                    perm.split('.')[-1]
                )
                access_fn = getattr(obj, access_fn_name, None)
                
                if not access_fn:
                    # No function defined on obj to determine access - assume
                    # access should be granted if no explicit object-level logic
                    # exists to determine otherwise
                    access = None
                else:
                    try:
                        if from_name == 'user':
                            access = access_fn(user_obj)
                        else:
                            access = access_fn(user_obj.groups.all())
                    except PermissionDenied:
                        access = False
            
            perm_cache[perm_cache_name] = access
        
        return perm_cache[perm_cache_name]
    
    def _get_object_permissions(self, user_obj, obj, from_name=None):
        """
        Return a set of the permissions a user has on a specific model object.
        ``from_name`` can be either "user" or "group", to determine permissions
        using the user object itself or the groups it belongs to, respectively.
        It can also be None to determine the users permissions from both sources.
        """
        
        if not obj or not user_obj.is_active or user_obj.is_anonymous:
            return set()
        
        perms_for_model = Permission.objects.filter(
            content_type__app_label=obj._meta.app_label,
            content_type__model=obj._meta.model_name,
        ).values_list('content_type__app_label', 'codename')
        
        perms_for_model = ['{0}.{1}'.format(app, name) for app, name in perms_for_model]
        
        if user_obj.is_superuser and not getattr(settings, 'DJEM_UNIVERSAL_OLP', False):
            # Superusers get all permissions, regardless of obj or from_name,
            # unless using "universal" OLP, in which case they are subject to
            # the same OLP logic as regular users
            perms = set(perms_for_model)
        else:
            perms = set()
            for perm in perms_for_model:
                user_access = None
                group_access = None
                
                # Check user first, unless only checking for group
                if from_name != 'group':
                    user_access = self._get_object_permission(perm, user_obj, obj, 'user')
                
                # Check group if user didn't grant the permission, unless only
                # checking for user
                if not user_access and from_name != 'user':
                    group_access = self._get_object_permission(perm, user_obj, obj, 'group')
                
                # The permission is granted if either of the user or group
                # checks grant it, or if neither of them have a defined
                # object-level access method
                if user_access or group_access or (user_access is None and group_access is None):
                    perms.add(perm)
        
        return perms
    
    def get_user_permissions(self, user_obj, obj=None):
        
        return self._get_object_permissions(user_obj, obj, 'user')
    
    def get_group_permissions(self, user_obj, obj=None):
        
        return self._get_object_permissions(user_obj, obj, 'group')
    
    def get_all_permissions(self, user_obj, obj=None):
        
        return self._get_object_permissions(user_obj, obj)
    
    def has_perm(self, user_obj, perm, obj=None):
        
        if not obj:
            return False  # not dealing with non-object permissions
        
        user_access = self._get_object_permission(perm, user_obj, obj, 'user')
        group_access = None
        
        # Check group if user didn't grant the permission
        if not user_access:
            group_access = self._get_object_permission(perm, user_obj, obj, 'group')
        
        # The permission is granted if either of the user or group
        # checks grant it, or if neither of them have a defined
        # object-level access method
        return user_access or group_access or (user_access is None and group_access is None)


def _check_perms(perms, user, view_kwargs):
    
    for perm in perms:
        if isinstance(perm, six.string_types):
            obj = None
        else:
            perm, obj_arg = perm  # expand two-tuple
            obj_pk = view_kwargs[obj_arg]
            
            # Get the model this permission belongs to
            try:
                perm_app, perm_code = perm.split('.')
                perm_obj = Permission.objects.get(
                    content_type__app_label=perm_app,
                    codename=perm_code
                )
            except (ValueError, Permission.DoesNotExist):
                # Treat malformed (missing a '.') or non-existent
                # permission names as permission denied
                raise PermissionDenied
            
            model = perm_obj.content_type.model_class()
            
            # Get the object instance using the inferred model and the
            # primary key passed to the view
            obj = get_object_or_404(model, pk=obj_pk)
            
            # Swap out the primary key with the instance itself in the view
            # kwargs, so the view doesn't have to query for it again
            view_kwargs[obj_arg] = obj
        
        if not user.has_perm(perm, obj):
            raise PermissionDenied


def permission_required(*perms, **kwargs):
    """
    Replacement for Django's ``permission_required`` decorator, providing
    support for object-level permissions. Instead of accepting either a string
    or an iterable of strings naming the permission/s to check, this version
    accepts multiple positional arguments, one for each permission to check.
    These arguments can be either strings or two-tuples. If two-tuples, the
    items should be:
      - a string naming the permission to check (in the <app label>.<permission code>
        format)
      - a string naming the keyword argument of the view that contains the
        primary key of the object to check the permission against
    
    Behaviour of the ``login_url`` and ``raise_exception`` keyword arguments is
    as per the original, except that the default value for ``raise_exception``
    can be specified with the ``DJEM_DEFAULT_403`` setting.
    """
    
    login_url = kwargs.pop('login_url', None)
    raise_exception = kwargs.pop('raise_exception', DEFAULT_403)
    
    def decorator(view_func):
        
        @wraps(view_func, assigned=available_attrs(view_func))
        def _wrapped_view(request, *args, **kwargs):
            
            # First, check if the user has the permission (even anon users)
            try:
                _check_perms(perms, request.user, kwargs)
            except PermissionDenied:
                # In case the 403 handler should be called, raise the exception
                if raise_exception:
                    raise
            else:
                return view_func(request, *args, **kwargs)
            
            # As the last resort, show the login form
            path = request.build_absolute_uri()
            resolved_login_url = resolve_url(login_url or settings.LOGIN_URL)
            
            # If the login url is the same scheme and net location then just
            # use the path as the "next" url.
            login_scheme, login_netloc = urlparse(resolved_login_url)[:2]
            current_scheme, current_netloc = urlparse(path)[:2]
            
            if ((not login_scheme or login_scheme == current_scheme) and
                    (not login_netloc or login_netloc == current_netloc)):
                path = request.get_full_path()
            
            from django.contrib.auth.views import redirect_to_login
            
            return redirect_to_login(path, resolved_login_url)
        
        return _wrapped_view
    
    return decorator


class PermissionRequiredMixin(DjangoPermissionRequiredMixin):
    """
    CBV mixin which verifies that the current user has all specified
    permissions, on the specified object where applicable.
    """
    
    raise_exception = DEFAULT_403
    
    def has_permission(self, view_kwargs):
        
        perms = self.get_permission_required()
        
        try:
            _check_perms(perms, self.request.user, view_kwargs)
        except PermissionDenied:
            return False
        else:
            return True
    
    # Overridden to pass kwargs to has_permission() and skip the immediate
    # parent's dispatch() when calling the super method (because it attempts
    # to call has_permission without the kwargs).
    def dispatch(self, request, *args, **kwargs):
        
        if not self.has_permission(kwargs):
            return self.handle_no_permission()
        
        return super(DjangoPermissionRequiredMixin, self).dispatch(request, *args, **kwargs)
