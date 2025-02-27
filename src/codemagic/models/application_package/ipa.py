import pathlib
import plistlib
import subprocess
import zipfile
from functools import lru_cache
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from codemagic.models.certificate import Certificate
from codemagic.models.export_options import ArchiveMethod
from codemagic.models.provisioning_profile import ProvisioningProfile

from .abstract_package import AbstractPackage


class Ipa(AbstractPackage):
    def _validate_package(self):
        try:
            return bool(self.info_plist)
        except zipfile.BadZipFile as bad_zip_file:
            raise IOError(f'Not a valid iOS application package at {self.path}') from bad_zip_file

    def _extract_file(self, filename_filter: Callable[[str], bool]) -> bytes:
        with zipfile.ZipFile(self.path) as zf:
            try:
                found_file_name = next(filter(filename_filter, zf.namelist()))
            except StopIteration:
                raise FileNotFoundError(filename_filter.__name__, self.path)

            try:
                with zf.open(found_file_name, 'r') as fd:
                    return fd.read()
            except zipfile.BadZipFile as e:
                if str(e) == 'Bad magic number for file header':
                    # Big ipas are compressed using lzfse compression format which adds extra bytes to Info-Zip
                    # Unfortunately Python's built-in zip library is not capable to handle those
                    pass
                else:
                    raise

            completed_process = subprocess.run(['unzip', '-p', self.path, found_file_name], capture_output=True)
            return completed_process.stdout

    def _get_app_file_contents(self, filename: str) -> bytes:
        """
        Return file descriptor for file in the zip container at ./Payload/<App-name>.app/<filename>
        """

        def filename_filter(path_name):
            return pathlib.Path(path_name).match(f'Payload/*.app/{filename}')

        filename_filter.__name__ = f'Payload/*.app/{filename}'
        return self._extract_file(filename_filter)

    def extract_app(self, target_directory: pathlib.Path) -> pathlib.Path:
        with zipfile.ZipFile(self.path) as zf:
            for zi in zf.filelist:
                path = pathlib.Path(zi.filename)
                try:
                    p1, p2, *_rest = path.parts
                except ValueError:
                    continue
                if p1 == 'Payload' and p2.endswith('.app'):
                    zf.extract(zi, path=target_directory)

            try:
                return next(pathlib.Path(target_directory).glob('Payload/*.app'))
            except StopIteration:
                raise IOError(f'Failed to extract Payload/*.app from {self.path}')

    @lru_cache(1)
    def _get_info_plist(self) -> Dict[str, Any]:
        info_plist_contents = self._get_app_file_contents('Info.plist')
        return plistlib.loads(info_plist_contents)

    @property
    def info_plist(self) -> Dict[str, Any]:
        # Mypy does not support decorated properties. Use cached getter to load the properties.
        return self._get_info_plist()

    @lru_cache(1)
    def _get_embedded_provisioning_profile(self) -> Optional[ProvisioningProfile]:
        try:
            embedded_mobileprovision_contents = self._get_app_file_contents('embedded.mobileprovision')
        except FileNotFoundError:
            return None
        return ProvisioningProfile.from_content(embedded_mobileprovision_contents)

    @property
    def embedded_provisioning_profile(self) -> Optional[ProvisioningProfile]:
        # Mypy does not support decorated properties. Use cached getter to load the profile.
        return self._get_embedded_provisioning_profile()

    @lru_cache(1)
    def _get_certificate(self) -> Optional[Certificate]:
        if self.embedded_provisioning_profile is None:
            return None
        return next(iter(self.embedded_provisioning_profile.certificates), None)

    @property
    def certificate(self) -> Optional[Certificate]:
        # Mypy does not support decorated properties. Use cached getter to load the certificate.
        return self._get_certificate()

    @property
    def bundle_identifier(self) -> str:
        """
        https://developer.apple.com/documentation/bundleresources/information_property_list/cfbundleidentifier
        """
        return self.info_plist['CFBundleIdentifier']

    @property
    def app_name(self) -> str:
        """
        https://developer.apple.com/documentation/bundleresources/information_property_list/cfbundledisplayname
        """
        return (
            self.info_plist.get('CFBundleDisplayName')
            or self.info_plist.get('CFBundleName')
            or ''
        )

    @property
    def version(self) -> str:
        """
        https://developer.apple.com/documentation/bundleresources/information_property_list/cfbundleshortversionstring
        """
        return (
            self.info_plist.get('CFBundleShortVersionString')
            or self.version_code
        )

    @property
    def version_code(self) -> str:
        """
        https://developer.apple.com/documentation/bundleresources/information_property_list/cfbundleversion
        """
        return self.info_plist.get('CFBundleVersion', '')

    @property
    def minimum_os_version(self) -> str:
        """
        https://developer.apple.com/documentation/bundleresources/information_property_list/minimumosversion
        """
        return self.info_plist.get('MinimumOSVersion', '')

    @property
    def supported_platforms(self) -> List[str]:
        return self.info_plist.get('CFBundleSupportedPlatforms', [])

    @property
    def archive_method(self) -> ArchiveMethod:
        profiles = [self.embedded_provisioning_profile] if self.embedded_provisioning_profile else []
        return ArchiveMethod.from_profiles(profiles)

    @property
    def provisioned_devices(self) -> List[str]:
        if self.embedded_provisioning_profile and self.embedded_provisioning_profile.provisioned_devices:
            return self.embedded_provisioning_profile.provisioned_devices
        return []

    @property
    def provisions_all_devices(self) -> bool:
        return bool(self.embedded_provisioning_profile and self.embedded_provisioning_profile.provisions_all_devices)

    def get_summary(self) -> Dict[str, Union[bool, Optional[str], List[str]]]:
        certificate_expires = None
        if self.certificate:
            certificate_expires = self.certificate.expires_at.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + '+0000'
        return {
            'app_name': self.app_name,
            'bundle_identifier': self.bundle_identifier,
            'certificate_expires': certificate_expires,
            'distribution_type': self.archive_method.value.replace('-', ' ').title(),
            'min_os_version': self.minimum_os_version,
            'provisioned_devices': self.provisioned_devices,
            'provisions_all_devices': self.provisions_all_devices,
            'supported_platforms': self.supported_platforms,
            'version': self.version,
            'version_code': self.version_code,
        }

    def is_for_tvos(self) -> bool:
        return any('tv' in platform_name.lower() for platform_name in self.supported_platforms)
