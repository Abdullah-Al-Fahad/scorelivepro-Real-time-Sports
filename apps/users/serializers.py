from django.utils import timezone
from datetime import timedelta
from django.contrib.auth import get_user_model, authenticate
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.conf import settings
from django.contrib.auth.models import Group, update_last_login

# Added send_verification_email here so the Login serializer can trigger it
from .utils import send_otp_via_email, send_verification_email, log_activity
from .models import FanProfile, AdminProfile, OneTimePassword, UserActivity

User = get_user_model()

# ==========================================
# 1. PROFILE & SETTINGS SERIALIZERS
# ==========================================

class FanProfileDetailSerializer(serializers.ModelSerializer):
    """
    READ-ONLY: Used to display full Fan details (with nested objects) inside UserProfile.
    """
    class Meta:
        model = FanProfile
        fields = ['favorite_teams', 'favorite_leagues', 'favorite_fixtures', 'receive_live_notifications', 'receive_news_updates', 'language']
        depth = 1 

class AdminProfileDetailSerializer(serializers.ModelSerializer):
    """
    READ-ONLY: Used to display Admin details inside UserProfile.
    """
    class Meta:
        model = AdminProfile
        fields = ['can_manage_news', 'can_manage_users']

class FanSettingsSerializer(serializers.ModelSerializer):
    """
    WRITE-ONLY: Used for the /profile/settings/ endpoint to update booleans.
    """
    class Meta:
        model = FanProfile
        fields = ['receive_live_notifications', 'receive_news_updates', 'language']

class AdminSettingsSerializer(serializers.ModelSerializer):
    """
    WRITE-ONLY: Used for the /profile/settings/ endpoint to update permissions.
    """
    class Meta:
        model = AdminProfile
        fields = ['can_manage_news', 'can_manage_users']

class ManageFavoriteSerializer(serializers.Serializer):
    """
    WRITE-ONLY: Used to Add/Remove items by ID.
    """
    id = serializers.IntegerField(required=True, help_text="The ID of the Team or League")

# ==========================================
# 2. MAIN USER SERIALIZER
# ==========================================

class UserProfileSerializer(serializers.ModelSerializer):
    """
    Main Profile Serializer.
    
    - GET: Returns User fields + nested 'profile_data' (Read-Only).
    - PATCH: Updates ONLY User fields (first_name, last_name, profile_image).
    """
    role = serializers.ReadOnlyField() 
    
    # This field is READ-ONLY. It will display data, but ignore any updates sent to it.
    profile_data = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            'id', 'email', 'role', 'first_name', 'last_name', 
            'profile_image', 'date_joined', 'last_login', 
            'profile_data'
        ]
        read_only_fields = ['id', 'email', 'role', 'date_joined', 'last_login', 'profile_data']

    def get_profile_data(self, obj):
        """
        Dynamically selects the detail serializer based on role.
        """
        if obj.role == 'Admin' and hasattr(obj, 'admin_profile'):
            return AdminProfileDetailSerializer(obj.admin_profile).data
        elif hasattr(obj, 'fan_profile'):
            return FanProfileDetailSerializer(obj.fan_profile).data
        return None

class UserActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = UserActivity
        fields = ['id', 'action', 'details', 'ip_address', 'timestamp']

# ==========================================
# 3. REGISTRATION & AUTH SERIALIZERS
# ==========================================

class UserRegistrationSerializer(serializers.ModelSerializer):
    # Override EmailField to remove the default UniqueValidator
    # We will handle uniqueness manually to allow re-registration of inactive accounts.
    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        write_only=True, 
        min_length=8, 
        style={'input_type': 'password'},
        help_text="Minimum 8 characters"
    )
    confirm_password = serializers.CharField(
        write_only=True, 
        min_length=8, 
        style={'input_type': 'password'},
        help_text="Must match the password field"
    )
    first_name = serializers.CharField(required=True)
    last_name = serializers.CharField(required=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'password', 'confirm_password']    

    def validate_email(self, value):
        user = User.objects.filter(email=value).first()
        # If user exists AND is active, block them. If inactive, let them pass.
        if user and user.is_active:
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate(self, data):
        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError({'password': 'Passwords do not match.'})
        return data

    def create(self, validated_data):
        validated_data.pop('confirm_password', None) 
        password = validated_data.pop('password')
        email = validated_data.get('email')
        
        user = User.objects.filter(email=email).first()
        
        if user and not user.is_active:
            # The user registered before but never verified. 
            # We update their details to the new registration attempt.
            user.set_password(password)
            user.first_name = validated_data.get('first_name', user.first_name)
            user.last_name = validated_data.get('last_name', user.last_name)
            user.save()
            log_activity(user, "SIGNUP_RETRY", "Account re-registered before activation")
            return user
        
        # Standard creation for a brand new email
        user = User.objects.create_user(
            password=password,
            is_active=False, 
            **validated_data
        )

        log_activity(user, "SIGNUP", "Account created via email registration")

        return user


class AdminRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8, style={'input_type': 'password'})
    confirm_password = serializers.CharField(write_only=True, min_length=8, style={'input_type': 'password'})
    first_name = serializers.CharField(required=True)
    last_name = serializers.CharField(required=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name', 'password', 'confirm_password']

    def validate(self, data):
        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError({'password': 'Passwords do not match.'})
        return data
    
    def create(self, validated_data):
        validated_data.pop('confirm_password', None)
        password = validated_data.pop('password')
        email = validated_data.pop('email')

        user = User.objects.create_user(
            email=email,
            password=password,
            is_active=True,
            is_staff=True,
            **validated_data
        )

        admin_group, _ = Group.objects.get_or_create(name='Admin')
        user.groups.add(admin_group)

        return user


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        email = attrs.get(self.username_field)
        password = attrs.get('password')

        # --- Intercept inactive users trying to log in ---
        if email:
            user = User.objects.filter(email=email).first()
            if user:
                # If they exist but haven't verified their email
                if not user.is_active:
                    # Verify their password to ensure they own the unverified account
                    if user.check_password(password):
                        # Resend the verification OTP automatically
                        send_verification_email(user)
                        # Tell the frontend to redirect them to the verification screen
                        raise ValidationError({
                            "detail": "Account is not verified. A new OTP has been sent to your email.",
                            "error_code": "account_unverified_otp_sent",
                            "email": user.email
                        })
                    else:
                        raise AuthenticationFailed("No active account found with the given credentials")

        # --- STANDARD LOGIC ---
        try:
            data = super().validate(attrs)
        except AuthenticationFailed:
            # Handle the Social Login case (user has no password)
            if email:
                user = User.objects.filter(email=email).first()
                if user and not user.has_usable_password():
                    send_otp_via_email(user.email, purpose='password_reset')
                    raise ValidationError({
                        "detail": "Account exists but has no password (Social Login). OTP sent.",
                        "error_code": "password_required_otp_sent",
                        "email": user.email 
                    })
            raise AuthenticationFailed("No active account found with the given credentials")

        if self.user.groups.filter(name='Admin').exists():
            raise serializers.ValidationError({"detail": "Admins must use the 2FA login endpoint."})
        
        update_last_login(None, self.user)
        log_activity(self.user, "LOGIN", "Logged in via Email/Password")

        data['user'] = {
            'id': self.user.id,
            'email': self.user.email,
            'first_name': self.user.first_name,
            'last_name': self.user.last_name,
            'role': self.user.role
        }
        return data

# ==========================================
# 4. ADMIN 2FA & OTP SERIALIZERS
# ==========================================

class AdminLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')
        user = authenticate(request=self.context.get('request'), email=email, password=password)

        if not user:
            raise AuthenticationFailed('Invalid credentials')
        if not user.groups.filter(name='Admin').exists():
            raise AuthenticationFailed('Access restricted to Admins only.')

        attrs['user'] = user
        return attrs

class AdminVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6)

    def validate(self, attrs):
        email = attrs.get('email')
        otp = attrs.get('otp')

        try:
            user = User.objects.get(email=email)
            user_otp = OneTimePassword.objects.get(user=user)
        except (User.DoesNotExist, OneTimePassword.DoesNotExist):
            raise AuthenticationFailed('Invalid OTP or user not found')

        if user_otp.otp != otp:
            raise AuthenticationFailed('Invalid OTP')
        if user_otp.created_at < timezone.now() - timedelta(minutes=5):
            user_otp.delete()
            raise AuthenticationFailed('OTP expired')

        attrs['user'] = user
        attrs['otp_obj'] = user_otp
        return attrs

class VerifyEmailSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6)

    def validate(self, attrs):
        email = attrs.get('email')
        otp = attrs.get('otp')

        try:
            user = User.objects.get(email=email)
            user_otp = OneTimePassword.objects.get(user=user)
        except (User.DoesNotExist, OneTimePassword.DoesNotExist):
            raise AuthenticationFailed('Invalid OTP or user')
        
        if user_otp.created_at < timezone.now() - timedelta(minutes=5):
            user_otp.delete() 
            raise AuthenticationFailed('OTP expired')

        if user_otp.otp != otp:
            raise AuthenticationFailed('Invalid OTP')
        
        attrs['user'] = user
        attrs['otp_obj'] = user_otp
        return attrs
    
    def save(self):
        user = self.validated_data['user']
        otp_obj = self.validated_data['otp_obj']
        if not user.is_active:
            user.is_active = True
            user.save()
        otp_obj.delete()
        return user

class ResendActivationEmailSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate(self, attrs):
        email = attrs.get('email')
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise serializers.ValidationError("User not found.")
        if user.is_active:
            raise serializers.ValidationError("Account already active.")
        attrs['user'] = user
        return attrs

class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    def validate_email(self, value):
        if not User.objects.filter(email=value).exists():
             raise serializers.ValidationError("User not found.")
        return value
    def save(self):
        send_otp_via_email(self.validated_data['email'], purpose='password_reset')

class VerifyPasswordResetOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6)

    def validate(self, attrs):
        email = attrs.get('email')
        otp = attrs.get('otp')

        try:
            user = User.objects.get(email=email)
            user_otp = OneTimePassword.objects.get(user=user)
        except (User.DoesNotExist, OneTimePassword.DoesNotExist):
            raise AuthenticationFailed('Invalid OTP or user not found.')

        if user_otp.otp != otp:
            raise AuthenticationFailed('Invalid OTP.')
        
        # Check if OTP is expired (5 minutes)
        if user_otp.created_at < timezone.now() - timedelta(minutes=5):
            user_otp.delete()
            raise AuthenticationFailed('OTP has expired.')

        return attrs

class SetNewPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(write_only=True, max_length=6)
    password = serializers.CharField(write_only=True, min_length=8, style={'input_type': 'password'})
    confirm_password = serializers.CharField(write_only=True, min_length=8, style={'input_type': 'password'})

    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError({'password': "Passwords do not match."})
        
        try:
            user = User.objects.get(email=attrs['email'])
            user_otp = OneTimePassword.objects.get(user=user)
        except (User.DoesNotExist, OneTimePassword.DoesNotExist):
            raise AuthenticationFailed('Invalid OTP or user not found.')

        if user_otp.otp != attrs['otp']:
             raise AuthenticationFailed('Invalid OTP.')
        
        # Check if OTP is expired (5 minutes)
        if user_otp.created_at < timezone.now() - timedelta(minutes=5):
            user_otp.delete()
            raise AuthenticationFailed('OTP has expired.')
        
        attrs['user'] = user
        attrs['otp_obj'] = user_otp 
        return attrs
    
    def save(self):
        user = self.validated_data['user']
        user.set_password(self.validated_data['password'])
        user.save()
        self.validated_data['otp_obj'].delete()
        log_activity(user, "PASSWORD_RESET", "Password was reset successfully")
        return user


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})
    new_password = serializers.CharField(write_only=True, required=True, min_length=8, style={'input_type': 'password'})
    confirm_password = serializers.CharField(write_only=True, required=True, min_length=8, style={'input_type': 'password'})

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Your old password is not correct.")
        return value

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError({"new_password": "New passwords do not match."})
        # Optional: Prevent changing to the exact same password
        if attrs['old_password'] == attrs['new_password']:
            raise serializers.ValidationError({"new_password": "The new password must be different from the old password."})
        return attrs

    def save(self):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save()
        log_activity(user, "PASSWORD_CHANGE", "User actively changed their password via profile settings", request=self.context.get('request'))
        return user

# ==========================================
# 5. ADMIN USER MANAGEMENT SERIALIZER
# ==========================================

class AdminUserManagementSerializer(serializers.ModelSerializer):
    """
    Used by Admins to view and update user accounts, including nested preferences.
    """
    role = serializers.ReadOnlyField()
    profile_data = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'role', 
            'is_active', 'is_staff', 'date_joined', 'last_login',
            'profile_data'
        ]
        # Prevent admins from accidentally changing core identity fields here
        read_only_fields = ['id', 'email', 'date_joined', 'last_login', 'role', 'profile_data']

    def get_profile_data(self, obj):
        """
        Dynamically selects the detail serializer based on role.
        """
        # obj.role evaluates groups. We use prefetch_related in the view to optimize this.
        if obj.role == 'Admin' and hasattr(obj, 'admin_profile'):
            return AdminProfileDetailSerializer(obj.admin_profile).data
        elif hasattr(obj, 'fan_profile'):
            return FanProfileDetailSerializer(obj.fan_profile).data
        return None
    

class BulkSyncRequestSerializer(serializers.Serializer):
    guest_id = serializers.CharField(required=False)
    device_id = serializers.CharField(required=False)

    def validate(self, attrs):
        if not attrs.get('guest_id') and not attrs.get('device_id'):
            raise serializers.ValidationError("Either 'guest_id' or 'device_id' is required.")
        if not attrs.get('guest_id'):
            attrs['guest_id'] = attrs['device_id']
        return attrs
