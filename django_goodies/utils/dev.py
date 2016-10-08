from django.contrib.auth import get_user_model


class Developer(object):
    
    def __init__(self, *kwargs):
        
        self.user_lookup_kwargs = kwargs
    
    @property
    def user(self):
        
        if not hasattr(self, '_user'):
            self._user = get_user_model().objects.get(self.user_lookup_kwargs)
        
        return self._user
    
    def be_awesome(self):
        
        user = self.user
        user.is_staff = True
        user.is_superuser = True
        user.save()
    
    def be_lame(self):
        
        user = self.user
        user.is_staff = False
        user.is_superuser = False
        user.save()
    
    def no_super(self):
        
        user = self.user
        user.is_superuser = False
        user.save()
    
    def no_staff(self):
        
        user = self.user
        user.is_staff = False
        user.save()
