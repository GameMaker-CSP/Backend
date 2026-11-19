import re
import jwt
from flask import Blueprint, app, request, jsonify, current_app, Response, g
from flask_restful import Api, Resource # used for REST API building
from flask_login import logout_user
from datetime import datetime, date
from __init__ import app, db
from api.authorize import token_required
from model.user import User
from model import account_types, guest_window
from model.github import GitHubUser
import os

user_api = Blueprint('user_api', __name__,
                   url_prefix='/api')

# API docs https://flask-restful.readthedocs.io/en/latest/api.html
api = Api(user_api)

class AccountDisplayPreferences(db.Model):
    __tablename__ = 'uesl_display_preferences'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    theme = db.Column(db.String(20), nullable=False)
    language = db.Column(db.String(8), nullable=False)
    nod = db.Column(db.Boolean, nullable=False)


class PortfolioPreferences(db.Model):
    __tablename__ = 'uesl_portfolio_preferences'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    options = db.Column(db.JSON, nullable=False)


def valid_portfolio_options(options):
    if not isinstance(options, dict):
        return False
    colors = {'bg', 'text', 'accent', 'selectionColor'}
    switches = {'focus', 'ruler', 'dyslexia', 'highContrast'}
    ranges = {'size': (12, 22), 'ttsRate': (.5, 2), 'ttsPitch': (.5, 2),
              'ttsVolume': (0, 1), 'sensitivity': (.1, .9)}
    choices = {'fontName': ('System', 'Roboto', 'Open Sans', 'Lato', 'Montserrat', 'Georgia', 'Source Code Pro'),
               'buttonStyle': ('rounded', 'square', 'pill'), 'mode': ('scroll', 'cursor')}
    for key, value in options.items():
        if key in colors:
            valid = isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value)
        elif key in switches:
            valid = type(value) is bool
        elif key in ranges:
            low, high = ranges[key]
            valid = type(value) in (float, int) and low <= value <= high
        elif key in choices:
            valid = value in choices[key]
        elif key == 'readingSize':
            valid = type(value) in (float, int) and (value == 0 or 10 <= value <= 28)
        elif key == 'ttsVoice':
            valid = isinstance(value, str) and len(value) <= 200
        else:
            return False
        if not valid:
            return False
    return True


class DisplayPreferences(Resource):
    @token_required()
    def get(self):
        row = db.session.get(AccountDisplayPreferences, g.current_user.id)
        result = {'theme': row.theme, 'language': row.language, 'nod': row.nod} if row else {
            'theme': 'Site Default', 'language': '', 'nod': False}

        if request.args.get('extended') == '1':
            detail = db.session.get(PortfolioPreferences, g.current_user.id)
            result['options'] = detail.options if detail else {}
        return result

    @token_required()
    def put(self):
        from sqlalchemy.dialects.sqlite import insert
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return {'message': 'Expected preference settings'}, 400
        theme, language, nod = (data.get(key) for key in ('theme', 'language', 'nod'))
        if (theme not in ('Site Default', 'Light', 'Green', 'Midnight', 'Sepia', 'Cyberpunk', 'Ocean')
                or language not in ('', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'zh-CN', 'zh-TW', 'ja', 'ko', 'ar', 'hi', 'vi', 'th', 'nl', 'pl', 'tr', 'uk', 'he', 'fa')
                or type(nod) is not bool):
            return {'message': 'Invalid display preferences'}, 400
        if 'options' in data and not valid_portfolio_options(data['options']):
            return {'message': 'Invalid portfolio preferences'}, 400
        values = dict(theme=theme, language=language, nod=nod)
        row = AccountDisplayPreferences(user_id=g.current_user.id, **values)
        if db.engine.dialect.name == 'sqlite':
            stmt = insert(AccountDisplayPreferences).values(user_id=g.current_user.id, **values)
            db.session.execute(stmt.on_conflict_do_update(index_elements=['user_id'], set_=values))
        else:
            db.session.merge(row)
        if 'options' in data:
            detail = dict(user_id=g.current_user.id, options=data['options'])
            if db.engine.dialect.name == 'sqlite':
                stmt = insert(PortfolioPreferences).values(**detail)
                db.session.execute(stmt.on_conflict_do_update(index_elements=['user_id'], set_={'options': stmt.excluded.options}))
            else:
                db.session.merge(PortfolioPreferences(**detail))
            values['options'] = data['options']
        db.session.commit()
        return values


api.add_resource(DisplayPreferences, '/user/preferences')


class UserAPI:        
    class _ID(Resource):  # Individual identification API operation
        @token_required()
        def get(self):
            ''' Retrieve the current user from the token_required authentication check '''
            current_user = g.current_user
            ''' Return the current user as a json object with role information '''
            user_data = current_user.public_details()
            # Add role information to response
            user_data['role'] = current_user.role
            user_data['is_admin'] = current_user.is_admin()
            user_data['is_teacher'] = current_user.is_teacher()
            return jsonify(user_data)
    
    class _BULK(Resource):  # Users API operation for Create, Read, Update, Delete 
        @token_required("Admin")
        def post(self):
            ''' Handle bulk user creation by sending POST requests to the single user endpoint '''
            users = request.get_json()
            
            if not isinstance(users, list):
                return {'message': 'Expected a list of user data'}, 400
            
            results = {'errors': []}
            
            with current_app.test_client() as client:
                for user in users:
                    # Set a default password as we don't have it for bulk creation
                    user["password"] = app.config['DEFAULT_PASSWORD']
                    
                    # Simulate a POST request to the single user creation endpoint
                    response = client.post('/api/user', json=user)
                    
                    if response.status_code != 200:  # Assuming success status code is 200
                        results['errors'].append(response.get_json())
                        continue
                
                    uid = user.get('uid')
                    user_obj = User.query.filter_by(_uid=uid).first()
                    # Process sections if provided
                    if user_obj is not None:
                        print("Creating:", user_obj.uid)
                        abbreviations = [section["abbreviation"] for section in user.get('sections', [])]
                        if len(abbreviations) > 0:  # Check if the list is not empty
                            section_obj = user_obj.add_sections(abbreviations)
                            if section_obj:
                                # update the year of the added sections
                                for section in user.get('sections'):
                                    user_obj.update_section(section)
                            else:
                                results['errors'].append({'message': f'Failed to add sections {abbreviations} to user {uid}'})
                                
            
            return jsonify(results) 
            
    class _CRUD(Resource):  # Users API operation for Create, Read, Update, Delete 
        def post(self): # Create method
            """
            Create a new user.

            Reads data from the JSON body of the request, validates the input, and creates a new user in the database.

            Returns:
                JSON response with the created user details or an error message.
            """
            
            # Read data for json body
            body = request.get_json()
            
            # Debug logging
            #print(f"Received signup request with body: {body}")
            
            ''' Avoid garbage in, error checking '''
            # validate name
            name = body.get('name')
            if name is None or len(name) < 2:
                return {'message': f'Name is missing, or is less than 2 characters'}, 400
            
            # validate uid
            uid = body.get('uid')
            if uid is None or len(uid) < 2:
                return {'message': f'User ID is missing, or is less than 2 characters'}, 400
          
            ''' User object creation '''
            #1: Setup minimal User object using __init__ method
            password = body.get('password')
            if password is not None:
                if len(password) < 8 and not password.startswith("pbkdf2:sha256:"):
                    return {'message': 'Password must be at least 8 characters'}, 400
                user_obj = User(name=name, uid=uid, password=password)
            else:
                user_obj = User(name=name, uid=uid)
            
            # Handle additional fields that frontend sends
            # Create a cleaned body with only the fields User model expects
            cleaned_body = {
                'name': name,
                'uid': uid,
                'password': password,
                'email': body.get('email'),
            }
            
            # Add optional fields if they exist
            if body.get('birthdate'):
                cleaned_body['birthdate'] = body.get('birthdate')
            if body.get('location'):
                cleaned_body['location'] = body.get('location')
            # If user is a minor (under 18), parent_email is required
            if body.get('birthdate'):
                try:
                    bdate = datetime.strptime(body.get('birthdate'), '%Y-%m-%d').date()
                    today = date.today()
                    age = today.year - bdate.year - ((today.month, today.day) < (bdate.month, bdate.day))
                    if age < 18:
                        parent_email = body.get('parent_email')
                        if not parent_email:
                            return {'message': 'Parent/guardian email is required for users under 18'}, 400
                        cleaned_body['parent_email'] = parent_email
                except ValueError:
                    return {'message': 'Invalid birthdate format. Use YYYY-MM-DD'}, 400
            if body.get('kasm_server_needed') is not None:
                cleaned_body['kasm_server_needed'] = body.get('kasm_server_needed')
            if body.get('auth_type') is not None:
                cleaned_body['auth_type'] = body.get('auth_type')
            # Support assigning classes (e.g. ["CSSE","CSP","CSA"]).
            # Accept either a list or a single string.
            if body.get('class') is not None:
                cleaned_body['class'] = body.get('class')
            
            # Remove None values
            cleaned_body = {k: v for k, v in cleaned_body.items() if v is not None}
            
            # print(f"Cleaned body for user creation: {cleaned_body}")

            #2: Save the User object to the database using custom create method
            try:
                user = user_obj.create(cleaned_body) # pass the cleaned body elements to be saved in the database
                #print(f"Create method returned: {user}")
                #print(f"User type: {type(user)}")
                
                if not user:
                    return {'message': 'User ID is unavailable'}, 409

                #print(f"Successfully created user: {user.uid}")
                # return response, the created user details as a JSON object
                return jsonify(user.public_details())
                
            except Exception as e:
                #print(f"Error creating user: {e}")
                return {'message': f'Error creating user: {str(e)}'}, 500

        @token_required()
        def get(self):
            """
            Retrieve all users.

            Retrieves a list of all users in the database.
            
            Query Parameters:
                page (int): Page number for pagination (starts at 1)
                per_page (int): Number of users per page (default: 50, max: 200)

            Returns:
                JSON response with a list of user dictionaries.
            """
            # retrieve the current user from the token_required authentication check  
            current_user = g.current_user
            
            # Get query parameters for pagination
            page = request.args.get('page', type=int)
            per_page = min(request.args.get('per_page', 50, type=int), 200)
            
            """ User SQLAlchemy query returning list of all users """
            query = User.query if current_user.is_staff() else User.query.filter_by(id=current_user.id)
            if page:
                # Paginated query
                pagination = query.paginate(page=page, per_page=per_page, error_out=False)
                users = pagination.items
                has_next = pagination.has_next
                has_prev = pagination.has_prev
                total = pagination.total
            else:
                users = query.all() # extract visible users from the database
                has_next = False
                has_prev = False
                total = len(users)
             
            # prepare a json list of user dictionaries
            json_ready = []  
            for user in users:
                user_data = user.public_details()
                # Add access control
                if current_user.role == 'Admin' or current_user.id == user.id:
                    user_data['access'] = ['rw'] # read-write access control 
                else:
                    user_data['access'] = ['ro'] # read-only access control 
                json_ready.append(user_data)
            
            # return response, a list of user dictionaries in JSON format
            if page:
                return jsonify({
                    'users': json_ready,
                    'pagination': {
                        'page': page,
                        'per_page': per_page,
                        'total': total,
                        'has_next': has_next,
                        'has_prev': has_prev
                    }
                })
            else:
                return jsonify(json_ready)
        
        @token_required()
        def put(self):
            """
            Update user details.

            Retrieves the current user from the token_required authentication check and updates the user details based on the JSON body of the request.

            Returns:
                JSON response with the updated user details or an error message.
            """
            
            # Retrieve the current user from the token_required authentication check
            current_user = g.current_user
            # Read data from the JSON body of the request
            body = request.get_json()

            ''' Admin-specific update handling '''
            if current_user.role == 'Admin':
                uid = body.get('uid')
                # Admin is updating themself
                if uid is None or uid == current_user.uid:
                    user = current_user 
                else: # Admin is updating another user
                    """ User SQLAlchemy query returning a single user """
                    user = User.query.filter_by(_uid=uid).first()
                    if user is None:
                        return {'message': f'User {uid} not found'}, 404
            else:
                # Non-admin can only update themselves
                user = current_user
                
            # Update the User object to the database using custom update method
            user.update(body)
            
            # return response, the updated user details as a JSON object
            return jsonify(user.public_details())
        
        @token_required("Admin")
        def delete(self):
            """
            Delete a user.

            Deletes a user from the database based on the JSON body of the request. Only accessible by Admin users.

            Returns:
                JSON response with a success message or an error message.
            """
            body = request.get_json()
            uid = body.get('uid')
            
            """ User SQLAlchemy query returning a single user """
            user = User.query.filter_by(_uid=uid).first()
            
            # bad request
            if user is None:
                return {'message': f'User {uid} not found'}, 404
           
            # Read and then Delete the User object using custom methods
            blocker = user.deletion_blocker(g.current_user)
            if blocker:
                return {"message": blocker[1]}, blocker[0]
            user_json = user.public_details()
            if not user.delete():
                return {"message": "Account still has linked records"}, 409
            
            # 204 is the status code for delete with no json response
            return f"Deleted user: {user_json}", 204 # use 200 to test with Postman
         
    class _Section(Resource):  # Section API operation
        @token_required()
        def get(self):
            ''' Retrieve the current user from the token_required authentication check '''
            current_user = g.current_user
            ''' Return the current user as a json object '''
            return jsonify(current_user.read_sections())
       
        @token_required() 
        def post(self):
            ''' Retrieve the current user from the token_required authentication check '''
            current_user = g.current_user
            
            ''' Read data for json body '''
            body = request.get_json()
            
            ''' Error checking '''
            sections = body.get('sections')
            if sections is None or len(sections) == 0:
                return {'message': f"No sections to add were provided"}, 400
            
            ''' Add sections'''
            if not current_user.add_sections(sections):
                return {'message': f'1 or more sections failed to add, current {sections} requested {current_user.read_sections()}'}, 404
            
            return jsonify(current_user.read_sections())
        
        @token_required()
        def put(self):
            ''' Retrieve the current user from the token_required authentication check '''
            current_user = g.current_user

            ''' Read data for json body '''
            body = request.get_json()

            ''' Error checking '''
            section_data = body.get('section')
            if not section_data:
                return {'message': 'Section data is required'}, 400
            
            if not section_data.get('abbreviation'):
                return {'message': 'Section abbreviation is required'}, 400
            
            if not section_data.get('year'):
                return {'message': 'Section year is required'}, 400

            ''' Update section year '''
            if not current_user.update_section(section_data):
                return {'message': f'Section {section_data.get("abbreviation")} not found or update failed'}, 404

            return jsonify(current_user.read_sections())
        
        @token_required()
        def delete(self):
            ''' Retrieve the current user from the token_required authentication check '''
            current_user = g.current_user
    
            ''' Read data for json body '''
            body = request.get_json()
    
            ''' Error checking '''
            sections = body.get('sections')
            if sections is None or len(sections) == 0:
                return {'message': f"No sections to delete were provided"}, 400
    
            ''' Remove sections '''
            if not current_user.remove_sections(sections):
                return {'message': f'1 or more sections failed to delete, current {sections} requested {current_user.read_sections()}'}, 404
    
            return {'message': f'Sections {sections} deleted successfully'}, 200
    class _Security(Resource):
        def post(self):
            try:
                body = request.get_json()
                if not body:
                    return {
                        "message": "Please provide user details",
                        "data": None,
                        "error": "Bad request"
                    }, 400
                ''' Get Data '''
                uid = body.get('uid')
                if uid is None:
                    return {'message': f'User ID is missing'}, 401
                password = body.get('password')
                if not password:
                    return {'message': f'Password is missing'}, 401
                            
                ''' Find user '''
    
                user = User.query.filter_by(_uid=uid).first()
                
                if user is None or not user.is_password(password):
                    return {"message": "Invalid user id or password"}, 401
                if user.expired() and guest_window.is_open():
                    user.activate()
                if not user.is_active:
                    
                    return {'message': f"Invalid user id or password"}, 401
                            
                # Check if user is found
                if user:
                    try:
                        token = jwt.encode(
                            {"_uid": user._uid, "token_version": user.token_version or 0},
                            current_app.config["SECRET_KEY"],
                            algorithm="HS256"
                        )
                        # Return JSON response with cookie
                        is_production = os.environ.get('IS_PRODUCTION', 'false').lower() == 'true'
                        
                        # Create JSON response
                        response_data = {
                            "message": f"Authentication for {user._uid} successful",
                            "user": {
                                "uid": user._uid,
                                "name": user.name,
                                "role": user.role,
                                "class": user._class if getattr(user, '_class', None) is not None else []
                            }
                        }
                        resp = jsonify(response_data)
                        
                        # Set cookie
                        if is_production:
                            resp.set_cookie(
                                current_app.config["JWT_TOKEN_NAME"],
                                token,
                                max_age=43200,  # 12 hours in seconds
                                secure=True,
                                httponly=True,
                                path='/',
                                samesite='Lax',
                                domain=None
                            )
                        else:
                            resp.set_cookie(
                                current_app.config["JWT_TOKEN_NAME"],
                                token,
                                max_age=43200,  # 12 hours in seconds
                                secure=False,
                                httponly=True,
                                path='/',
                                samesite='Lax'
                            )
                        return resp 
                    except Exception as e:
                        return {
                                        "error": "Something went wrong",
                                        "message": str(e)
                                    }, 500
                return {
                                "message": "Error fetching auth token!",
                                "data": None,
                                "error": "Unauthorized"
                            }, 404
            except Exception as e:
                 return {
                                "message": "Something went wrong!",
                                "error": str(e),
                                "data": None
                            }, 500
                 
        def delete(self):
            ''' Clear the JWT cookie and Flask-Login session to log out — no token required '''
            logout_user()
            resp = Response("Logged out successfully")
            is_production = os.environ.get('IS_PRODUCTION', 'false').lower() == 'true'
            if is_production:
                resp.set_cookie(
                    current_app.config["JWT_TOKEN_NAME"],
                    '',
                    max_age=0,
                    secure=True,
                    httponly=True,
                    path='/',
                    samesite='Lax',
                    domain=None
                )
            else:
                resp.set_cookie(
                    current_app.config["JWT_TOKEN_NAME"],
                    '',
                    max_age=0,
                    secure=False,
                    httponly=True,
                    path='/',
                    samesite='Lax'
                )
            return resp

    class _GradeData(Resource):
        """
        Grade data API operations
        """
        
        @token_required()
        def get(self):
            """
            Get the grade data for a user.
            """
            current_user = g.current_user
            
            # If request includes a UID parameter and user is admin, get that user's grade data
            uid = request.args.get('uid')
            if current_user.role == 'Admin' and uid:
                user = User.query.filter_by(_uid=uid).first()
                if not user:
                    return {'message': f'User {uid} not found'}, 404
            else:
                user = current_user  # Get the current user's grade data
                
            return jsonify({'uid': user.uid, 'grade_data': user.grade_data})
        
        @token_required()
        def post(self):
            """
            Add or update grade data for a user.
            """
            current_user = g.current_user
            body = request.get_json()
            
            # Determine which user's grade data to update
            uid = body.get('uid')
            if current_user.role == 'Admin' and uid:
                user = User.query.filter_by(_uid=uid).first()
                if not user:
                    return {'message': f'User {uid} not found'}, 404
            else:
                # Non-admins can only update their own grade data
                if uid and uid != current_user.uid and current_user.role != 'Admin':
                    return {'message': 'Permission denied: You can only update your own grade data'}, 403
                user = current_user
            
            # Get the grade data from the request
            grade_data = body.get('grade_data')
            if not grade_data:
                return {'message': 'Grade data is missing'}, 400
                
            # Update the user's grade data
            user.update({'grade_data': grade_data})
            
            return jsonify({'message': 'Grade data updated successfully', 'uid': user.uid, 'grade_data': user.grade_data})

    class _APExam(Resource):
        """
        AP exam data API operations
        """
        
        @token_required()
        def get(self):
            """
            Get the AP exam data for a user.
            """
            current_user = g.current_user
            
            # If request includes a UID parameter and user is admin, get that user's AP exam data
            uid = request.args.get('uid')
            if current_user.role == 'Admin' and uid:
                user = User.query.filter_by(_uid=uid).first()
                if not user:
                    return {'message': f'User {uid} not found'}, 404
            else:
                user = current_user  # Get the current user's AP exam data
                
            return jsonify({'uid': user.uid, 'ap_exam': user.ap_exam})
        
        @token_required()
        def post(self):
            """
            Add or update AP exam data for a user.
            """
            current_user = g.current_user
            body = request.get_json()
            
            # Determine which user's AP exam data to update
            uid = body.get('uid')
            if current_user.role == 'Admin' and uid:
                user = User.query.filter_by(_uid=uid).first()
                if not user:
                    return {'message': f'User {uid} not found'}, 404
            else:
                # Non-admins can only update their own AP exam data
                if uid and uid != current_user.uid and current_user.role != 'Admin':
                    return {'message': 'Permission denied: You can only update your own AP exam data'}, 403
                user = current_user
            
            # Get the AP exam data from the request
            ap_exam = body.get('ap_exam')
            if not ap_exam:
                return {'message': 'AP exam data is missing'}, 400
                
            # Update the user's AP exam data
            user.update({'ap_exam': ap_exam})
            
            return jsonify({'message': 'AP exam data updated successfully', 'uid': user.uid, 'ap_exam': user.ap_exam})

    class _School(Resource):
        """
        Location data API operations
        """

        @token_required()
        def get(self):
            """
            Get the location data for a user.
            """
            current_user = g.current_user

            # If request includes a UID parameter and user is admin, get that user's location data
            uid = request.args.get('uid')
            if current_user.role == 'Admin' and uid:
                user = User.query.filter_by(_uid=uid).first()
                if not user:
                    return {'message': f'User {uid} not found'}, 404
            else:
                user = current_user  # Get the current user's location data

            return jsonify({'uid': user.uid, 'location': user.location})

        @token_required()
        def post(self):
            """
            Add or update location data for a user.
            """
            current_user = g.current_user
            body = request.get_json()

            # Determine which user's location data to update
            uid = body.get('uid')
            if current_user.role == 'Admin' and uid:
                user = User.query.filter_by(_uid=uid).first()
                if not user:
                    return {'message': f'User {uid} not found'}, 404
            else:
                # Non-admins can only update their own location data
                if uid and uid != current_user.uid and current_user.role != 'Admin':
                    return {'message': 'Permission denied: You can only update your own location data'}, 403
                user = current_user

            # Get the location data from the request
            location = body.get('location')
            if not location:
                return {'message': 'Location data is missing'}, 400

            # Update the user's location data
            user.update({'location': location})

            return jsonify({'message': 'Location data updated successfully', 'uid': user.uid, 'location': user.location})

    class _GuestCRUD(Resource):
        """
        Guest user API operations - simplified signup without GitHub validation
        """

        def post(self):
            """
            Create a new guest user account.

            Accepts only username (uid) and password. Auto-generates required fields.
            No GitHub validation required for guest accounts.

            Returns:
                JSON response with the created user details or an error message.
            """
            if not guest_window.is_open():
                return {"message": "Guest registration is closed"}, 403
            body = request.get_json()
            if not isinstance(body, dict) or not all(isinstance(body.get(k), str) and len(body[k].strip()) >= 2 for k in ('uid', 'password')):
                return {"message": "Supply a username and password"}, 400
            user = User(name=body['uid'], uid=body['uid'], password=body['password'], role='Guest')
            user.last_verified = datetime.utcnow()
            if not user.create():
                return {"message": "Username unavailable"}, 409
            return jsonify(uid=user.uid, role=user.role)

    class _VerifyAccount(Resource):
        """Teacher/Admin verifies any account: marks it active and records last_verified.
        How long that covers is fixed for some roles (see model/dataconfigs/account_types.json); for the
        others the optional JSON body {"days": N} says (1 up to the full interval), and
        default_verification_days applies when it is left out."""
        @token_required(account_types.staff_roles())
        def post(self, uid):
            target = User.query.filter_by(_uid=uid).first()
            if target is None:
                return {'message': f'User {uid} not found'}, 404
            if target.is_admin() and not g.current_user.is_admin():
                return {"message": "An admin must verify this account"}, 403
            if account_types.allowed_requests(target.role) is not None:
                # verifying would restart, or end, the few hours a limited account (a guest) lasts
                return {'message': 'This type of account cannot be verified'}, 400
            days = account_types.fixed_verification_days(target.role)
            if days is None:
                body = request.get_json(silent=True)
                if body is not None and not isinstance(body, dict):
                    return {'message': 'The body must be a JSON object such as {"days": 30}'}, 400
                days = (body or {}).get('days', account_types.default_verification_days())
            interval = account_types.verification_interval_days()
            if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= interval:
                return {'message': f'days must be a whole number from 1 to {interval}'}, 400
            target.activate(days)
            return jsonify(uid=target.uid, active=target.active)

    class _DeactivateAccount(Resource):
        """Teacher/Admin marks an account inactive. The account and its data are kept."""
        @token_required(account_types.staff_roles())
        def post(self, uid):
            target = User.query.filter_by(_uid=uid).first()
            if target is None:
                return {'message': f'User {uid} not found'}, 404
            blocker = target.deactivation_blocker(g.current_user)
            if blocker:
                return {'message': blocker[1]}, blocker[0]
            target.deactivate()
            return jsonify(uid=target.uid, active=target.active)

    class _ChangeRole(Resource):
        """Teacher/Admin changes another account's role: POST {"role": <a role from account_types.json>}. Only an Admin can
        make someone an Admin or change an Admin's role; nobody changes their own role."""
        @token_required(account_types.staff_roles())
        def post(self, uid):
            target = User.query.filter_by(_uid=uid).first()
            if target is None:
                return {'message': f'User {uid} not found'}, 404
            body = request.get_json(silent=True)
            role = body.get('role') if isinstance(body, dict) else None
            blocker = target.role_change_blocker(g.current_user, role)
            if blocker:
                return {'message': blocker[1]}, blocker[0]
            target.role = role
            db.session.commit()
            return jsonify(uid=target.uid, role=target.role)

    class _GuestWindow(Resource):
        """The Guest Window (see model/guest_window.py). Anyone can ask whether it is open. A
        Teacher/Admin opens it with {"minutes": N}, N up to window_max_minutes in
        model/dataconfigs/account_types.json, and can close it at any time."""
        def get(self):
            closes = guest_window.closes_at()
            return {'open': closes is not None, 'closes_at': None if closes is None else closes.isoformat()}

        @token_required(account_types.staff_roles())
        def post(self):
            body = request.get_json(silent=True)
            minutes = body.get('minutes') if isinstance(body, dict) else None
            longest = account_types.window_max_minutes()
            if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= longest:
                return {'message': f'minutes must be a whole number from 1 to {longest}'}, 400
            closes = guest_window.open_for(minutes)
            # %r quotes the uid, so a newline in it cannot forge a second log line
            current_app.logger.info("AUDIT guest window opened by uid=%r for %d minutes", g.current_user.uid, minutes)
            return {'open': True, 'closes_at': closes.isoformat()}

        @token_required(account_types.staff_roles())
        def delete(self):
            guest_window.close()
            current_app.logger.info("AUDIT guest window closed by uid=%r", g.current_user.uid)
            return {'open': False, 'closes_at': None}

    class _GuestDisable(Resource):
        """Teacher/Admin turns guest access off at once: closes the Guest Window and makes every
        active guest inactive, so each is refused from their next request. The records are kept,
        and a guest can come back in a later window."""
        @token_required(account_types.staff_roles())
        def post(self):
            guest_window.close()
            guests = User.query.filter(User._role.in_(account_types.guest_roles()), User.active.is_(True)).all()
            for guest in guests:
                guest.token_version = (guest.token_version or 0) + 1
                guest.active = False
                guest.last_verified = None  # so coming back counts as returning after the time has passed
            db.session.commit()
            current_app.logger.info("AUDIT guest access disabled by uid=%r, %d guest accounts made inactive", g.current_user.uid, len(guests))
            return {'open': False, 'closes_at': None, 'disabled': len(guests)}

    class _StaffCreate(Resource):
        @token_required(account_types.staff_roles())
        def post(self):
            data = request.get_json()
            if not isinstance(data, dict):
                return {"message": "Supply account details"}, 400
            kind = data.get("accountType")
            role = account_types.staff_created().get(kind) if isinstance(kind, str) else None
            if not role or role not in g.current_user.grantable_roles():
                return {"message": "Choose a staff-created account type"}, 400
            if not all(isinstance(data.get(key), str) and data[key].strip() for key in ("name", "uid", "password")) or len(data["password"]) < 8:
                return {"message": "Supply a name, user ID and password of at least eight characters"}, 400
            user = User(name=data["name"].strip(), uid=data["uid"].strip(), password=data["password"], role=role)
            user.active = False
            if not user.create():
                return {"message": "User ID unavailable"}, 409
            return jsonify(uid=user.uid, role=user.role, active=user.active)

    api.add_resource(_StaffCreate, '/user/admin-create')
    api.add_resource(_VerifyAccount, '/user/<string:uid>/verify')
    api.add_resource(_DeactivateAccount, '/user/<string:uid>/deactivate')
    api.add_resource(_ChangeRole, '/user/<string:uid>/role')
    api.add_resource(_GuestWindow, '/guest/window')
    api.add_resource(_GuestDisable, '/guest/disable')
    api.add_resource(_ID, '/id')
    api.add_resource(_BULK, '/users')
    api.add_resource(_CRUD, '/user')
    api.add_resource(_GuestCRUD, '/user/guest')
    api.add_resource(_Section, '/user/section')
    api.add_resource(_Security, '/authenticate')
    api.add_resource(_GradeData, '/grade_data')
    api.add_resource(_APExam, '/apexam')
    api.add_resource(_School, '/school')
    
    class _Class(Resource):
        """Manage the user's `class` list (e.g. CSSE, CSP, CSA).
        GET: return classes
        POST: add or remove classes (use 'action' parameter: 'add', 'remove', or 'clear')
        PUT: replace classes
        """

        @token_required()
        def get(self):
            current_user = g.current_user
            uid = request.args.get('uid')
            if current_user.role == 'Admin' and uid:
                user = User.query.filter_by(_uid=uid).first()
                if not user:
                    return {'message': f'User {uid} not found'}, 404
            else:
                user = current_user

            return jsonify({'uid': user.uid, 'class': user._class if getattr(user, '_class', None) is not None else []})

        @token_required()
        def post(self):
            """Add, remove, or clear classes from the user's class list based on action parameter."""
            current_user = g.current_user
            body = request.get_json() or {}
            uid = body.get('uid')
            if current_user.role == 'Admin' and uid:
                user = User.query.filter_by(_uid=uid).first()
                if not user:
                    return {'message': f'User {uid} not found'}, 404
            else:
                user = current_user

            action = body.get('action', 'add')  # Default to 'add' for backward compatibility
            
            if action == 'clear':
                # Clear all classes
                user.update({'class': []})
                return {'message': f'Cleared classes for {user.uid}'}, 200
            
            classes = body.get('class') or body.get('classes')
            if not classes:
                return {'message': f'No classes provided to {action}'}, 400

            if isinstance(classes, str):
                classes = [classes]

            existing = user._class if getattr(user, '_class', None) is not None else []
            
            if action == 'add':
                # Add classes (merge without duplicates)
                new_list = existing + [c for c in classes if c not in existing]
                user.update({'class': new_list})
                return jsonify({'uid': user.uid, 'class': user._class})
            
            elif action == 'remove':
                # Remove specified classes
                remaining = [c for c in existing if c not in classes]
                user.update({'class': remaining})
                return jsonify({'uid': user.uid, 'class': user._class})
            
            else:
                return {'message': f'Invalid action: {action}. Use "add", "remove", or "clear"'}, 400

        @token_required()
        def put(self):
            """Replace the user's class list with the provided list."""
            current_user = g.current_user
            body = request.get_json() or {}
            uid = body.get('uid')
            if current_user.role == 'Admin' and uid:
                user = User.query.filter_by(_uid=uid).first()
                if not user:
                    return {'message': f'User {uid} not found'}, 404
            else:
                user = current_user

            classes = body.get('class') or body.get('classes')
            if classes is None:
                return {'message': 'class list is required for PUT'}, 400
            if isinstance(classes, str):
                classes = [classes]

            user.update({'class': classes})
            return jsonify({'uid': user.uid, 'class': user._class})



    api.add_resource(_Class, '/user/class')
