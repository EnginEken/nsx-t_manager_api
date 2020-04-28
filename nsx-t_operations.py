import re
import slack
import json
from decouple import config
import schedule
import time
import requests
import math

api_url = 'https://nsxman.hepsiburada.com/policy/api/v1/infra/'
api_url_fabric = 'https://nsxman.hepsiburada.com/api/v1/fabric/'

request_headers = {"Authorization": config('auth_token'),
                   "Content-Type": "application/json",
                   }

data = {
  "subnets": [{
        "gateway_address": ""
  }],
  "vlan_ids": [
      ""
  ],
  "connectivity_path": "",
  "transport_zone_path": "",
  "advanced_config": {
        "address_pool_paths": [],
        "hybrid": False,
        "local_egress": False,
        "connectivity": "ON"
      },
  "display_name": ""
}
tag_data = {
    "external_id": "",
    "tags": [
        {"scope": "", "tag": ""}
    ]
}

connectivity_path_base = "/infra/tier-1s/"
transport_zone_base = "/infra/sites/default/enforcement-points/default/transport-zones/"

CHANNEL = "CV50KQYU9"  # "GUNJ6RQD8"
MESSAGES_PER_PAGE = 1

segment_names = []

client = slack.WebClient(token=config('SLACK_TOKEN'))

page = 1


def is_segment_created(url):
    response = requests.get(url, verify=False, headers=request_headers)
    if response.status_code == 200:
        return True
    else:
        return False

def is_vm_tagged(url):
    response = requests.get(url, verify=False, headers=request_headers).json()['results']
    for i in range(len(response)):
        for key, value in response[i].items():
            if key == 'tags':
                if not value:
                    return False
                else:
                    return True

class nsxAPI(object):

    def __init__(self, request_headers=request_headers, api_url=api_url, data=data, tag_data=tag_data,
                 api_url_fabric=api_url_fabric,
                 connectivity_path_base=connectivity_path_base, transport_zone_base=transport_zone_base):
        super(nsxAPI, self).__init__()
        self.api_url = api_url
        self.api_url_fabric = api_url_fabric
        self.headers = request_headers
        self.data = data
        self.connectivity_path_base = connectivity_path_base
        self.transport_zone_base = transport_zone_base
        self.tag_data = tag_data

    def create_overlay_segment(self, segment_id, segment_display_name, subnet, connectivity_path,
                               transport_zone_overlay):

        api_overlay_url = self.api_url + "segments/" + segment_id
        segment_created = is_segment_created(api_overlay_url)

        if segment_created:
            print("Segment is already created with the same id")
            client.chat_postMessage(
                channel=CHANNEL,
                text="Segment that you are trying to create is already exist"
            )
        elif not segment_created:
            overlay_data = self.data.copy()
            del overlay_data['vlan_ids']
            overlay_data['subnets'] = [{"gateway_address": subnet}]
            overlay_data['connectivity_path'] = self.connectivity_path_base + connectivity_path + "-T1-Router"
            overlay_data['transport_zone_path'] = self.transport_zone_base + transport_zone_overlay
            overlay_data['display_name'] = segment_display_name

            response = requests.patch(api_overlay_url, verify=False, headers=self.headers, json=overlay_data)
            print(response.status_code)
            return response.status_code

    def create_vlan_segment(self, segment_id, segment_display_name, vlan, transport_zone_vlan):

        api_vlan_url = self.api_url + "segments/" + segment_id
        segment_created = is_segment_created(api_vlan_url)
        if segment_created:
            print("Segment is already created with the same id")
        elif not segment_created:
            vlan_data = self.data.copy()
            del vlan_data['subnets']
            del vlan_data['connectivity_path']
            vlan_data['vlan_ids'] = [vlan]
            vlan_data['transport_zone_path'] = self.transport_zone_base + transport_zone_vlan
            vlan_data['display_name'] = segment_display_name

            response = requests.patch(api_vlan_url, verify=False, headers=self.headers, json=vlan_data)
            print(response.status_code)
            return response.status_code

    def get_all_segments(self):
        return requests.get(self.api_url + "segments/", verify=False, headers=self.headers).json()['results']

    def get_virtual_machines(self):
        virtual_machines_url = self.api_url_fabric + 'virtual-machines?cursor='
        cursor = []
        all_vms = []
        cursor.append("")

        loop_range = requests.get(virtual_machines_url, verify=False, headers=request_headers).json()['result_count']
        loop_range = math.floor(loop_range/1000)

        for i in range(loop_range):
            response = requests.get(virtual_machines_url + str(cursor[i]), verify=False, headers=request_headers).json()
            cursor.append(response['cursor'])
            all_vms.append(response['results'])
        all_vms.append(
            requests.get(virtual_machines_url + str(cursor[4]), verify=False, headers=request_headers).json()[
                'results'])
        return all_vms

    def delete_segment(self, segment_id):
        url = self.api_url + "segments/" + segment_id
        requests.delete(url, verify=False, headers=self.headers)
        response = requests.delete(url, verify=False, headers=self.headers)
        return response.status_code

    def assign_tag(self, tag_name, external_id):
        info = "tagged"
        assign_tag_url = self.api_url_fabric + 'virtual-machines?action=update_tags'
        tag_check_url = self.api_url_fabric + 'virtual-machines?external_id=' + external_id
        new_tag_data = self.tag_data.copy()
        new_tag_data['external_id'] = external_id
        new_tag_data['tags'] = [{"scope": "security", "tag": tag_name}]
        vm_tagged = is_vm_tagged(tag_check_url)
        if vm_tagged:
            return True
        elif not vm_tagged:
            requests.post(assign_tag_url, verify=False, headers=request_headers, json=new_tag_data)
            vm_tagged = is_vm_tagged(tag_check_url)
            if vm_tagged:
                return info
            elif not vm_tagged:
                return False

    def group_check_create(self, action, group_id):
        group_data = {
            "expression": [
                {
                    "member_type": "VirtualMachine",
                    "value": group_id,
                    "key": "Tag",
                    "operator": "EQUALS",
                    "resource_type": "Condition"
                }
            ],
            "description": group_id,
            "display_name": group_id
        }

        group_api_url = self.api_url + "domains/default/groups/" + group_id
        if action == "check":
            response = requests.get(group_api_url, verify=False, headers=request_headers)
            if response.status_code == 404:
                return False
            elif response.status_code == 200:
                return True
        elif action == "create":
            response = requests.patch(group_api_url, verify=False, headers=request_headers, json=group_data)
            if response.status_code == 200:
                return True
            else:
                return False


def assign_new_tag(last_message):
    vm_display_search = []
    vm_display_dict = {}
    vm_exist = False
    tag_name = ""
    vm_site = ""

    last_message[1] = str(last_message[1]).lower()
    if last_message[1] == "tag":
        all_virtual_machines = nsx_api.get_virtual_machines()
        if len(last_message) == 4:
            vm_name = last_message[2]
            print(vm_name)
            counter = 0
            for i in range(len(all_virtual_machines)):
                for vm in all_virtual_machines[i]:
                    counter = counter + 1
                    if str(vm_name).lower() == vm['display_name'].lower():
                        vm_external_id = vm['external_id']
                        vm_exist = True
                        break
                    else:
                        counter = counter - 1
            if counter == 0:
                vm_exist = False

            if vm_exist:
                vm_name_search = re.sub('\d+$', '', vm_name)
                suggested_vm_name = vm_name_search
                vm_name_search = re.split("\W+|_", vm_name_search)
                vm_name_search = list(filter(None, vm_name_search))
                counter = 1
                length = len(vm_name_search)
                while counter < length:
                    if str(vm_name_search[-1]).isdigit():
                        vm_name_search = vm_name_search[:-1]
                        counter = counter + 1
                    else:
                        break

                if len(vm_name_search) == 1:
                    for y in range(len(all_virtual_machines)):
                        for vm in all_virtual_machines[y]:
                            if str(vm_name_search[0]).lower() in vm['display_name'].lower():
                                vm_display_gecici = re.sub('\d+$', '', vm['display_name'])
                                vm_display_gecici = re.split("\W+|_", vm_display_gecici)
                                vm_display_gecici = list(filter(None, vm_display_gecici))
                                if len(vm_display_gecici) == 1:
                                    vm_display_search.append(vm['display_name'])

                elif len(vm_name_search) > 1:
                    for i in range(2):
                        if vm_display_search:
                            for y in range(len(vm_display_search)):
                                gecici = re.sub('\d+$', '', vm_display_search[y])
                                gecici = re.split("\W+|_", gecici)
                                gecici = list(filter(None, gecici))
                                counter2 = 1
                                length2 = len(gecici)
                                while counter2 < length2:
                                    if str(gecici[-1]).isdigit():
                                        gecici = gecici[:-1]
                                        counter2 = counter2 + 1
                                    else:
                                        break

                                if len(vm_name_search) != len(gecici):
                                    vm_display_search[y] = ""
                                else:
                                    counter3 = 1
                                    for z in range(len(gecici) - 1):
                                        if str(vm_name_search[counter3]) == str(gecici[counter3]):
                                            counter3 = counter3 + 1
                                        elif str(vm_name_search[counter3]) != str(gecici[counter3]):
                                            vm_display_search[y] = vm_display_search[y]
                                            break
                                    if counter3 == len(vm_name_search):
                                        continue
                                    else:
                                        vm_display_search[y] = ""

                        elif not vm_display_search:
                            for y in range(len(all_virtual_machines)):
                                for vm in all_virtual_machines[y]:
                                    gecici = re.sub('\d+$', '', vm['display_name'])
                                    gecici = re.split("\W+|_", gecici)
                                    gecici = list(filter(None, gecici))
                                    counter2 = 1
                                    length2 = len(gecici)
                                    while counter2 < length2:
                                        if str(gecici[-1]).isdigit():
                                            gecici = gecici[:-1]
                                            counter = counter + 1
                                        else:
                                            break
                                    if str(vm_name_search[0]).lower() == str(gecici[0].lower()):
                                        vm_display_search.append(vm['display_name'])

                vm_display_search = list(filter(None, vm_display_search))

                for i in range(len(vm_display_search)):
                    if str(vm_display_search[i]).lower() == str(vm_name).lower():
                        vm_display_search.remove(vm_display_search[i])
                        break

                if vm_display_search:
                    for i in range(len(vm_display_search)):
                        vm_display_dict[i + 1] = {}
                        vm_display_dict[i + 1]['display_name'] = vm_display_search[i]
                        for y in range(len(all_virtual_machines)):
                            for vm in all_virtual_machines[y]:
                                if str(vm_display_search[i]).lower() == str(vm['display_name']).lower():
                                    try:
                                        vm_display_dict[i + 1]['tags'] = vm['tags']
                                    except:
                                        pass
                                    for key, value in vm['source'].items():
                                        if key == "target_display_name":
                                            if value[:2] == "av":
                                                vm_display_dict[i + 1]['site'] = "avrupa"
                                            elif value[:2] == "as":
                                                vm_display_dict[i + 1]['site'] = "asya"

                    for i in range(len(all_virtual_machines)):
                        for vm in all_virtual_machines[i]:
                            if vm['display_name'] == vm_name:
                                for key, value in vm['source'].items():
                                    if key == "target_display_name":
                                        if value[:2] == "av":
                                            vm_site = "avrupa"
                                        elif value[:2] == "as":
                                            vm_site = "asya"
                                        print("Girilen vm in site ı ", vm_site)
                    counter4 = 0
                    for i in range(len(vm_display_dict)):
                        if vm_display_dict[i + 1]['site'] == vm_site:
                            try:
                                for y in vm_display_dict[i + 1]['tags']:
                                    tag_name = y['tag']
                            except KeyError:
                                pass
                            if tag_name:
                                break
                        else:
                            counter4 = counter4 + 1

                    if tag_name:
                        response = nsx_api.assign_tag(tag_name, vm_external_id)
                        if response:
                            client.chat_postMessage(
                                channel=CHANNEL,
                                text="This VM is already tagged. Please be sure that it is not tagged."
                            )
                        elif not response:
                            client.chat_postMessage(
                                channel=CHANNEL,
                                text="VM is not tagged but I also can not tag the VM. Please try again."
                            )
                        elif response == "tagged":
                            client.chat_postMessage(
                                channel=CHANNEL,
                                text="VM is tagged with the proper tag successfully!"
                            )

                    if counter4 == len(vm_display_search):
                        print("Benzer vm var ama siteları aynı olan bulamadım")
                        for i in vm_display_dict[1]['tags']:
                            group_id_old = i['tag']
                            group_id = re.sub('(^\w+(?=\_))|(^\w+(?=\-))', vm_site, i['tag'])
                        client.chat_postMessage(
                            channel=CHANNEL,
                            text="There is similar named VMs but their data centers are different than your VM's."
                                 "\nTheir tag is `" + group_id_old + "`. Please type `assign tag" + group_id +
                                 "<vm_name> <mention_me>` if tag name is okay for you. If it is not, simply change the" 
                                 "tag name in the message."
                        )

                    print("Girilen Vm e assign edilmesi gereken tag ", tag_name)
                    print(json.dumps(vm_display_dict, indent=4))

                elif not vm_display_search:
                    for i in range(len(all_virtual_machines)):
                        for vm in all_virtual_machines[i]:
                            if vm['display_name'] == vm_name:
                                for key, value in vm['source'].items():
                                    if key == "target_display_name":
                                        if value[:2] == "av":
                                            vm_site = "avrupa"
                                        elif value[:2] == "as":
                                            vm_site = "asya"
                                        print("Girilen vm in site ı ", vm_site)

                    suggested_vm_name = re.sub('(\W+|_)$', '', suggested_vm_name)
                    suggested_group = vm_site + "_" + str(suggested_vm_name).lower()

                    client.chat_postMessage(
                        channel=CHANNEL,
                        text="VM that you have created is unique. I am suggesting you to assign `" + suggested_group +
                             "` tag to VM. \nIf you are okay with this please try to send `assign tag "
                             "<tag_name> <vm_name> <mention_me>` or simply change the `tag_name` with whatever you "
                             "want.\nNew group will be created with the name you typed."
                    )

            elif not vm_exist:
                client.chat_postMessage(
                    channel=CHANNEL,
                    text="VM name can not be found in current VM list!\n" + "Please check the VM name!"
                )

        elif len(last_message) == 5:
            tag_name = last_message[2]
            vm_name = last_message[3]
            print(tag_name, vm_name)
            counter = 0
            for i in range(len(all_virtual_machines)):
                for vm in all_virtual_machines[i]:
                    counter = counter + 1
                    if str(vm_name).lower() == vm['display_name'].lower():
                        vm_external_id = vm['external_id']
                        vm_exist = True
                        break
                    else:
                        counter = counter - 1
            if counter == 0:
                vm_exist = False
            if vm_exist:
                response = nsx_api.group_check_create('check', tag_name)
                if response:
                    client.chat_postMessage(
                        channel=CHANNEL,
                        text="There is already a group with this name. I am tagging VM with it..."
                    )
                    print("bu isimde tag zaten var. VM i bu tag ile taglıyorum")
                    response_tag = nsx_api.assign_tag(tag_name, vm_external_id)
                    if response_tag:
                        client.chat_postMessage(
                            channel=CHANNEL,
                            text="This VM is already tagged. Please be sure that it is not tagged."
                        )
                        print("vm zaten taglı kardeş")
                    elif response_tag == "tagged":
                        client.chat_postMessage(
                            channel=CHANNEL,
                            text="VM is tagged successfully!"
                        )
                        print("VM güzelce taglandı kardeş")
                    elif not response_tag:
                        client.chat_postMessage(
                            channel=CHANNEL,
                            text="VM is not tagged but I also can not tag the VM. Please try again."
                        )
                        print("vm taglı değildi ama ben de taglayamadım kardeş")

                elif not response:
                    client.chat_postMessage(
                        channel=CHANNEL,
                        text="There is no group with this name. I am creating it..."
                    )
                    print("bu isimde grup yok, create ediyorum")
                    response_group = nsx_api.group_check_create('create', tag_name)
                    if response_group:
                        response_tag = nsx_api.assign_tag(tag_name, vm_external_id)
                        if response_tag:
                            client.chat_postMessage(
                                channel=CHANNEL,
                                text="This VM is already tagged. Please be sure that it is not tagged."
                            )
                            print("vm zaten taglı kardeş")
                        elif response_tag == "tagged":
                            client.chat_postMessage(
                                channel=CHANNEL,
                                text="VM is tagged with created group successfully!"
                            )
                            print("VM güzelce taglandı kardeş")
                        elif not response_tag:
                            client.chat_postMessage(
                                channel=CHANNEL,
                                text="Group is created and VM isn't tagged but I  can not tag the VM. Please try again."
                            )
                            print("vm taglı değildi ama ben de taglayamadım kardeş")

                    elif not response_group:
                        client.chat_postMessage(
                            channel=CHANNEL,
                            text="I couldn't create the group. Please try again."
                        )
                        print("grup create edilemedi kardeş. Bence biraz sonra tekrar dene")

            elif not vm_exist:
                client.chat_postMessage(
                    channel=CHANNEL,
                    text="VM name can not be found in current VM list!\n" + "Please check the VM name!"
                )
            """
            vm_name in external_id sini al. VMi tag name ile tagla. O grup var mı diye kontrol et, varsa tagla
            yoksa grubu create et sonra tagla.
            """


def vlan_overlay_segment_delete(last_message):
    segment_display_name = ""
    segment_id = ""
    segment_text = ""
    last_message[1] = str(last_message[1]).lower()
    if last_message[1] == "segment":
        all_segments = nsx_api.get_all_segments()
        last_message.pop(0)
        last_message.pop(0)
        last_message.pop()
        for i in range(len(last_message)):
            segment_display_name = segment_display_name + str(last_message[i])
            segment_display_name = segment_display_name + " "
        segment_display_name = segment_display_name[:-1]
        print(segment_display_name)
        for values in all_segments:
            segment_names.append(values['display_name'])
            if values['display_name'] == segment_display_name:
                segment_id = values['id']
                is_segment_exist = True
                break
            else:
                is_segment_exist = False

        if is_segment_exist:
            http_response = nsx_api.delete_segment(segment_id)
            if http_response == 200:
                client.chat_postMessage(
                    channel=CHANNEL,
                    text="Segment is deleted successfully."
                )

        elif not is_segment_exist:
            for i in range(len(segment_names)):
                segment_text = segment_text + str(segment_names[i]) + "\n"
            client.files_upload(
                channels=CHANNEL,
                initial_comment="There is no segment created with this name. You can find the created segments list" 
                                "below:",
                content=segment_text
            )


def vlan_segment_create(last_message):
    """
    Get the segment name and VLAN tag info from the message. Call the nsxAPI class' vlan segment creation function
    and create vlan segment with nsx-t API. Post the informational message to channel.
    :param last_message:
    :return:
    """
    segment_display_name = last_message[2]
    segment_id = last_message[2]
    vlan = str(last_message[3])
    transport_zone_vlan = "2ffb3b05-38ad-4b3e-9300-232d778e872a"
    http_response = nsx_api.create_vlan_segment(segment_id, segment_display_name, vlan, transport_zone_vlan)
    if http_response == 200:
        client.chat_postMessage(
            channel=CHANNEL,
            text="Vlan segment is created successfully!"
        )


def overlay_segment_create(last_message):
    """
    Get the segment name and IP subnet info from the message. Call the nsxAPI class' overlay segment creation function
    and create overlay segment with nsx-t API. Post the informational message to channel.
    :param last_message:
    :return:
    """
    segment_display_name = ""
    segment_id = ""
    for i in range(3):
        segment_id = segment_id + str(last_message[i + 2])
        segment_display_name = segment_display_name + str(last_message[i + 2])
        segment_display_name = segment_display_name + " "
    segment_display_name = segment_display_name[:-1]
    subnet = last_message[5]
    connectivity_path = re.split("\s", segment_display_name)
    connectivity_path = list(filter(None, connectivity_path))[0]
    transport_zone_overlay = "f12a62b9-99a7-410f-b220-8fc947cd08c4"
    http_response = nsx_api.create_overlay_segment(segment_id, segment_display_name, subnet, connectivity_path,
                                                   transport_zone_overlay)
    if http_response == 200:
        client.chat_postMessage(
            channel=CHANNEL,
            text="Overlay segment is created successfully!"
        )


def read_last_message():
    """
    Last message and the user who is typed that message in the channel will be read.
    if last message is typed by network admins, NSX-T overlay/vlan segment can be created or deleted with the message.
    if last message is typed by other employees, they can only assign tag to the created VM
    :return:
    """
    print("Retrieving page {}".format(page))
    response = client.conversations_history(
        channel=CHANNEL,
        limit=MESSAGES_PER_PAGE,
        #count=MESSAGES_PER_PAGE client.groups_history
    )
    assert response["ok"]
    messages_all = response['messages']

    with open('messages.json', 'w', encoding='utf-8') as f:
              json.dump(
                      messages_all,
                      f,
                      sort_keys=True,
                      indent=4,
                      ensure_ascii=False
        )
    with open('messages.json', 'r', encoding='utf-8') as json_file:
                last_message = json.load(json_file)
                for i in last_message:
                        last_message = i['text']
                        user = i["user"]

    last_message = re.split("\s", last_message)
    last_message = list(filter(None, last_message))
    print(last_message)
    print(user)

    if last_message[-1] == '<@U0101EJK493>':

        if user == "UQQDTTHCN" or user == "UH2QH6WHM":
            last_message[0] = str(last_message[0]).lower()

            if last_message[0] == "create":
                last_message[1] = str(last_message[1]).lower()

                if last_message[1] == "overlay":
                    overlay_segment_create(last_message)

                elif last_message[1] == "vlan":
                    vlan_segment_create(last_message)

            elif last_message[0] == "assign":
                assign_new_tag(last_message)

            elif last_message[0] == "delete":
                vlan_overlay_segment_delete(last_message)
        else:
            assign_new_tag(last_message)


if __name__ == "__main__":
    """
    Creating class element to use it later.
    Schedule every 2 seconds read_last_message function. This function is reading last message from channel and
    calling the proper function. 
    """
    nsx_api = nsxAPI(request_headers=request_headers, api_url=api_url, data=data, tag_data=tag_data,
                     api_url_fabric=api_url_fabric, connectivity_path_base=connectivity_path_base,
                     transport_zone_base=transport_zone_base)

    schedule.every(2).seconds.do(read_last_message)

    while True:
        schedule.run_pending()
        time.sleep(1)
