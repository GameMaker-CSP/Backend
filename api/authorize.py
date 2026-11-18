from collections import deque
from flask import request, current_app, g
from flask_login import current_user
from functools import wraps
import jwt
import threading
import time
from model.user import User
from model import account_types

# When each limited account (a guest) made its recent requests, to hold it to requests_per_minute in
# account_types.json. Kept in this process, so with several workers an account can make that many
# requests a minute on each: it limits one account flooding the server, it does not meter it exactly.
_recent = {}
_recent_lock = threading.Lock()


def throttled(user):
    """True when this account has made its requests_per_minute in the last minute. A request is
    counted once, however many checks it passes through."""
    limit = account_types.requests_per_minute(user.role)
    if limit is None or getattr(g, "request_counted", False):
        return False
    g.request_counted = True
    now = time.monotonic()
    with _recent_lock:
        times = _recent.setdefault(user.id, deque())
        while times and now - times[0] >= 60:
            times.popleft()
        if len(times) >= limit:
            return True
        times.append(now)
        if len(_recent) > 1000:  # forget accounts that have gone quiet
            for key in [k for k, v in _recent.items() if not v or now - v[-1] >= 60]:
                del _recent[key]
    return False


def auth_required(roles=None):
    '''
    Hybrid authentication decorator supporting both session and JWT token authentication.
    
    This function guards API endpoints by:
      1. First checking for Flask-Login session authentication (current_user)
      2. If no session, checks for valid JWT token in request cookies
      3. Decodes the token and retrieves user data from database
      4. Validates user has required role(s) if specified
      5. Sets g.current_user in Flask's global context for use in decorated function
      6. Returns the decorated function if all checks pass
    
    Authentication priority:
      - Session authentication (Flask-Login) is checked first (faster)
      - JWT token authentication is fallback (stateless, works for APIs)
    
    Args:
        roles: String or list of allowed roles (e.g., "Admin" or ["Admin", "Teacher"])
               If None, any authenticated user is allowed
    
    Possible error responses:
      A. 401 / Unauthorized: no session and token is missing or invalid
      B. 403 / Forbidden: user has insufficient permissions
      C. 500 / Internal Server Error: something went wrong with token decoding
    '''
    def decorator(func_to_guard):
        @wraps(func_to_guard)
        def decorated(*args, **kwargs):
            user = None
            auth_method = None
            
            # Method 1: JWT token cookie / Bearer header (always checked first for API calls)
            token = request.cookies.get(current_app.config.get("JWT_TOKEN_NAME"))
            # Also check Authorization: Bearer <token> header
            if not token:
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]

            if not token:
                # Method 2: Fall back to Flask-Login session (no JWT present at all)
                if current_user.is_authenticated:
                    user = current_user
                    auth_method = "session"
                    g.current_user = user
                else:
                    return {
                        "message": "Authentication required. No session or token found.",
                        "data": None,
                        "error": "Unauthorized"
                    }, 401
            else:
                try:
                    # Decode the JWT token
                    data = jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])
                    user = User.query.filter_by(_uid=data["_uid"]).first()

                    if user is None or data.get("token_version", 0) != (user.token_version or 0):
                        return {
                            "message": "Invalid Authentication token!",
                            "data": None,
                            "error": "Unauthorized"
                        }, 401

                    auth_method = "jwt"
                    g.current_user = user

                except jwt.ExpiredSignatureError:
                    return {
                        "message": "Token has expired!",
                        "data": None,
                        "error": "Unauthorized"
                    }, 401
                except jwt.InvalidTokenError:
                    return {
                        "message": "Invalid token!",
                        "data": None,
                        "error": "Unauthorized"
                    }, 401
                except Exception as e:
                    return {
                        "message": "Something went wrong decoding the token!",
                        "data": None,
                        "error": str(e)
                    }, 500
            
            # At this point, user is authenticated via either session or JWT
            if not user.is_active or not account_types.request_allowed(user.role, request.method, request.path):
                return {"message": "Account access restricted"}, 403
            if throttled(user):
                return {"message": "Too many requests. Wait a minute."}, 429, {"Retry-After": "60"}
            # Now check role requirements if specified
            if roles:
                # Normalize roles to list for consistent checking
                required_roles = [roles] if isinstance(roles, str) else roles
                
                if user.role not in required_roles:
                    return {
                        "message": f"Insufficient permissions. Required roles: {', '.join(required_roles)}",
                        "data": None,
                        "error": "Forbidden"
                    }, 403
            
            # If this is a CORS preflight request, return 200 OK immediately
            if request.method == 'OPTIONS':
                return ('', 200)
            
            # Success - user is authenticated and authorized
            # func_to_guard is the function decorated with @auth_required
            # Returns with the original function arguments
            return func_to_guard(*args, **kwargs)
        
        return decorated
    
    return decorator


# Alias for backward compatibility with existing code using token_required
def token_required(roles=None):
    '''
    Backward compatibility alias for auth_required.
    Existing code using @token_required will continue to work.
    '''
    return auth_required(roles)
