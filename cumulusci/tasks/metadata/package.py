import io
import os
import re
import urllib

import xml.etree.ElementTree as ET

import yaml

from cumulusci.core.tasks import BareTask

__location__ = os.path.realpath(
    os.path.join(os.getcwd(), os.path.dirname(__file__)))

def metadata_sort_key(name):
    sections = []
    for section in re.split('[.|-]', name):
        sections.append(metadata_sort_key_section(section))

    key = '_'.join(sections)
    key = key.replace('_','Z')

    return key

def metadata_sort_key_section(name):
    prefix = '5'
    key = name

    # Sort namespace prefixed names last
    base_name = name
    if base_name.endswith('__c'):
        base_name = base_name[:-3]
    if base_name.find('__') != -1:
        prefix = '8'

    key = prefix + name
    return key

class MetadataParserMissingError(Exception):
    pass

class PackageXmlGenerator(object):
    def __init__(self, directory, api_version, package_name=None, managed=None, delete=None, install_class=None,
                 uninstall_class=None):
        with open(__location__ + '/metadata_map.yml', 'r') as f_metadata_map:
            self.metadata_map = yaml.load(f_metadata_map)
        self.directory = directory
        self.api_version = api_version
        self.package_name = package_name
        self.managed = managed
        self.delete = delete
        self.install_class = install_class
        self.uninstall_class = uninstall_class
        self.types = []


    def __call__(self):
        self.parse_types()
        return self.render_xml()

    def parse_types(self):
        for item in os.listdir(self.directory):
            if item == 'package.xml':
                continue
            if not os.path.isdir(self.directory + '/' + item):
                continue
            if item.startswith('.'):
                continue
            config = self.metadata_map.get(item)
            if not config:
                raise MetadataParserMissingError('No parser configuration found for subdirectory %s' % item)

            for parser_config in config:
                if parser_config.get('options'):
                    parser = globals()[parser_config['class']](
                        parser_config['type'],                # Metadata Type
                        self.directory + '/' + item,          # Directory
                        parser_config.get('extension', ''),   # Extension
                        self.delete,                          # Parse for deletion?
                        **parser_config.get('options', {})    # Extra kwargs
                    )
                else:
                    parser = globals()[parser_config['class']](
                        parser_config['type'],                # Metadata Type
                        self.directory + '/' + item,          # Directory
                        parser_config.get('extension', ''),   # Extension
                        self.delete,                          # Parse for deletion?
                    )

                self.types.append(parser)

    def render_xml(self):
        lines = []


        # Print header
        lines.append(u'<?xml version="1.0" encoding="UTF-8"?>')
        lines.append(u'<Package xmlns="http://soap.sforce.com/2006/04/metadata">')
        if self.package_name:
            package_name_encoded = urllib.quote(self.package_name, safe=' ')
            lines.append(u'    <fullName>{0}</fullName>'.format(package_name_encoded))

        if self.managed and self.install_class:
            lines.append(u'    <postInstallClass>{0}</postInstallClass>'.format(self.install_class))

        if self.managed and self.uninstall_class:
            lines.append(u'    <uninstallClass>{0}</uninstallClass>'.format(self.uninstall_class))

        # Print types sections
        self.types.sort(key=lambda x: x.metadata_type.upper())
        for parser in self.types:
            type_xml = parser()
            if type_xml:
                lines.extend(type_xml)

        # Print footer
        lines.append(u'    <version>{0}</version>'.format(self.api_version))
        lines.append(u'</Package>')

        return u'\n'.join(lines)

class BaseMetadataParser(object):

    def __init__(self, metadata_type, directory, extension, delete):
        self.metadata_type = metadata_type
        self.directory = directory
        self.extension = extension
        self.delete = delete
        self.members = []

        if self.delete:
            self.delete_excludes = self.get_delete_excludes()

    def __call__(self):
        self.parse_items()
        return self.render_xml()

    def get_delete_excludes(self):
        filename = os.path.join(__location__, '..', '..', 'files', 'metadata_whitelist.txt')
        excludes = []
        with open(filename, 'r') as f:
            for line in f:
                excludes.append(line.strip())
        return excludes

    def parse_items(self):
        # Loop through items
        for item in os.listdir(self.directory):
            # on Macs this file is generated by the OS. Shouldn't be in the package.xml
            if item.startswith('.'):
                continue

            if self.extension and not item.endswith('.' + self.extension):
                continue

            if item.endswith('-meta.xml'):
                continue

            if self.check_delete_excludes(item):
                continue

            self.parse_item(item)

    def check_delete_excludes(self, item):
        if not self.delete:
            return False
        if item in self.delete_excludes:
            return True
        return False

    def parse_item(self, item):
        members = self._parse_item(item)
        if members:
            self.members.extend(members)

    def _parse_item(self, item):
        "Receives a file or directory name and returns a list of members"
        raise NotImplemented("Subclasses should implement their parser here")

    def strip_extension(self, filename):
        return '.'.join(filename.split('.')[:-1])

    def render_xml(self):
        output = []
        if not self.members:
            return
        output.append(u'    <types>')
        self.members.sort(key=lambda x: metadata_sort_key(x))
        for member in self.members:
            try:
                member = unicode(member, 'utf-8')
            except TypeError:
                # Assume member is already unicode
                pass
            output.append(u'        <members>{0}</members>'.format(member))
        output.append(u'        <name>{0}</name>'.format(self.metadata_type))
        output.append(u'    </types>')
        return output


class MetadataFilenameParser(BaseMetadataParser):

    def _parse_item(self, item):
        return [self.strip_extension(item)]


class MetadataFolderParser(BaseMetadataParser):

    def _parse_item(self, item):
        members = []
        path = self.directory + '/' + item

        # Skip non-directories
        if not os.path.isdir(path):
            return members

        # Add the member if it is not namespaced
        if item.find('__') == -1:
            members.append(item)

        for subitem in os.listdir(path):
            if subitem.endswith('-meta.xml') or subitem.startswith('.'):
                continue
            submembers = self._parse_subitem(item, subitem)
            members.extend(submembers)

        return members

    def check_delete_excludes(self, item):
        return False

    def _parse_subitem(self, item, subitem):
        return [item + '/' + self.strip_extension(subitem)]

class MissingNameElementError(Exception):
    pass

class ParserConfigurationError(Exception):
    pass

class MetadataXmlElementParser(BaseMetadataParser):

    namespaces = {'sf': 'http://soap.sforce.com/2006/04/metadata'}

    def __init__(self, metadata_type, directory, extension, delete, item_xpath=None, name_xpath=None):
        super(MetadataXmlElementParser, self).__init__(metadata_type, directory, extension, delete)
        if not item_xpath:
            raise ParserConfigurationError('You must provide a value for item_xpath')
        self.item_xpath = item_xpath
        if not name_xpath:
            name_xpath = './sf:fullName'
        self.name_xpath = name_xpath

    def _parse_item(self, item):
        root = ET.parse(self.directory + '/' + item)
        members = []

        parent = self.strip_extension(item)

        for item in self.get_item_elements(root):
            members.append(self.get_item_name(item, parent))

        return members

    def check_delete_excludes(self, item):
        return False

    def get_item_elements(self, root):
        return root.findall(self.item_xpath, self.namespaces)

    def get_name_elements(self, item):
        return item.findall(self.name_xpath, self.namespaces)

    def get_item_name(self, item, parent):
        """ Returns the value of the first name element found inside of element """
        names = self.get_name_elements(item)
        if not names:
            raise MissingNameElementError

        name = names[0].text
        prefix = self.item_name_prefix(parent)
        if prefix:
            name = prefix + name

        return name

    def item_name_prefix(self, parent):
        return parent + '.'

# TYPE SPECIFIC PARSERS

class CustomLabelsParser(MetadataXmlElementParser):
    def item_name_prefix(self, parent):
        return ''

class CustomObjectParser(MetadataFilenameParser):
    def _parse_item(self, item):
        members = []

        # Skip namespaced custom objects
        if len(item.split('__')) > 2:
            return members

        # Skip standard objects
        if not item.endswith('__c.object') and \
           not item.endswith('__mdt.object') and \
           not item.endswith('__e.object') and \
           not item.endswith('__b.object'):
            return members

        members.append(self.strip_extension(item))
        return members

class RecordTypeParser(MetadataXmlElementParser):
    def check_delete_excludes(self, item):
        if self.delete:
            return True

class BusinessProcessParser(MetadataXmlElementParser):
    def check_delete_excludes(self, item):
        if self.delete:
            return True

class AuraBundleParser(MetadataFilenameParser):
    def _parse_item(self, item):
        if item.startswith('.'):
            return []
        return [item]

class DocumentParser(MetadataFolderParser):
    def _parse_subitem(self, item, subitem):
        return [item + '/' + subitem]

class UpdatePackageXml(BareTask):
    task_options = {
        'path': {
            'description': 'The path to a folder of metadata to build the package.xml from',
            'required': True,
        },
        'output': {
            'description': 'The output file, defaults to <path>/package.xml',
        },
        'package_name': {
            'description': 'If set, overrides the package name inserted into the <fullName> element',
        },
        'managed': {
            'description': 'If True, generate a package.xml for deployment to the managed package packaging org',
        },
        'delete': {
            'description': 'If True, generate a package.xml for use as a destructiveChanges.xml file for deleting metadata',
        },
    }

    def _init_options(self, kwargs):
        super(UpdatePackageXml, self)._init_options(kwargs)
        if 'managed' not in self.options:
            self.options['managed'] = False
        if self.options['managed'] in [True, 'True']:
            self.options['managed'] = True
        

    def _init_task(self):
        package_name = None
        if 'package_name' in self.options:
            package_name = self.options['package_name']
        else:
            if self.options.get('managed') in [True, 'True', 'true']:
                package_name = self.project_config.project__package__name_managed
            if not package_name:
                package_name = self.project_config.project__package__name

        self.package_xml = PackageXmlGenerator(
            directory = self.options.get('path'),
            api_version = self.project_config.project__package__api_version,
            package_name = package_name,
            managed = self.options.get('managed', False),
            delete = self.options.get('delete', False),
            install_class = self.project_config.project__package__install_class,
            uninstall_class = self.project_config.project__package__uninstall_class,
        )

    def _run_task(self):
        output = self.options.get('output', '{}/package.xml'.format(self.options.get('path')))
        self.logger.info('Generating {} from metadata in {}'.format(output, self.options.get('path')))
        package_xml = self.package_xml()
        with io.open(
                self.options.get('output', output),
                mode='w',
                encoding='utf-8',
            ) as f:
            f.write(package_xml)
