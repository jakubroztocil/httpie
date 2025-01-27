import json
import os


TEMPLATE_FILE = "httpie/cli/templates.json"


def store_json_template(args):
    """
    Store a template as a string in templates.json, the format for running this is:
        http template <name> |<method>| <url> login=testuser password=testpassword ...
    """
    template_name = args.pop(0)
    template_method = None
    template_url = None
    template_variables = {}

    if ["CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "POST", "PUT", "TRACE", "PATCH"].__contains__(args[0].upper()):
        template_method = args.pop(0)
        template_url = args.pop(0)
    else:
        temp = args.pop(0)
        print(f"'{temp}' is not a valid http method, defaulting to null...")
        template_url = args.pop(0)

    for arg in args:
        if '=' in arg:
            variable_name, variable_value = arg.split("=")
            template_variables[variable_name] = variable_value

    template = {}
    template['method'] = template_method
    template['url'] = template_url
    template['data'] = template_variables

    # Check if the templates.json file exists
    if not os.path.isfile(TEMPLATE_FILE):
        open(TEMPLATE_FILE, "w").close()

    with open(TEMPLATE_FILE, "r+") as f:
        stored_templates = {}
        try:
            stored_templates = json.load(f)
        except json.JSONDecodeError:
            pass
        if template_name in stored_templates:
            stored_templates.pop(template_name)
        stored_templates[template_name] = template
        f.seek(0)
        json.dump(stored_templates, f)
        f.truncate()


def edit_json_template(args):
    """
    Edit a template in templates.json, the format for running this is:
        http editt <name> <item> <value>
    """
    stored_templates = {}

    # Check if the templates.json file exists
    if not os.path.isfile(TEMPLATE_FILE):
        open(TEMPLATE_FILE, "w").close()

    with open(TEMPLATE_FILE, "r+") as f:
        try:
            stored_templates = json.load(f)
        except json.JSONDecodeError:
            pass

        template_name = args.pop(0)
        template_item = args.pop(0)
        template_value = args.pop(0)

        # Check if the template exists
        if template_name not in stored_templates:
            print(f"Template '{template_name}' does not exist.")
            return

        # Update the HTTP method
        if template_item == 'method':
            stored_templates[template_name]['method'] = template_value.upper()
        # Update the URL
        elif template_item == 'url':
            stored_templates[template_name]['url'] = template_value
        # Update a key-value pair in the data dictionary
        elif template_item in stored_templates[template_name]['data']:
            stored_templates[template_name]['data'][template_item] = template_value
        # Add a new key-value pair to the data dictionary
        else:
            stored_templates[template_name]['data'][template_item] = template_value

        # Save the updated template to file
        f.seek(0)
        json.dump(stored_templates, f)
        f.truncate()


def delete_template(arg):
    """
    Tries to delete the template with name 'arg'
    Usage format: http delt <arg>
    """
    stored_templates = {}

    # Check if the templates.json file exists
    if not os.path.isfile(TEMPLATE_FILE):
        open(TEMPLATE_FILE, "w").close()

    with open(TEMPLATE_FILE, "r+") as f:
        try:
            stored_templates = json.load(f)
        except json.JSONDecodeError:
            pass

        # Check if the template exists
        if arg not in stored_templates:
            print(f"Template '{arg}' does not exist.")
            return
        del stored_templates[arg]
        # Save the updated template to file
        f.seek(0)
        json.dump(stored_templates, f)
        f.truncate()


def load_template(arg):
    """
    Load a template from templates.json and return a list of arguments to be passed to the main function
    the format for running this is:
        http runt <template>
    """

    # Check if the templates.json file exists
    if not os.path.isfile(TEMPLATE_FILE):
        open(TEMPLATE_FILE, "w").close()

    with open(TEMPLATE_FILE, "r+") as f:
        stored_templates = {}
        try:
            stored_templates = json.load(f)
        except json.JSONDecodeError:
            pass
        args = []
        args_dict = None
        try:
            args_dict = stored_templates[arg]
        except KeyError:
            print(f"Template '{arg}' does not exist.")
        if args_dict is not None:
            args.append(args_dict.pop('method'))
            args.append(args_dict.pop('url'))
            data_dict = args_dict.pop('data')
            for key, value in data_dict.items():
                args.append(key + "=" + value)
        return args
